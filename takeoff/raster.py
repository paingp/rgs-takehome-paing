"""Source -> Raster, and Source -> DZI tile pyramid. The rasterization boundary.

A source is a PDF or an image file. The goal is to count symbols on ANY rasterized drawing,
so a scan is a first-class input, not a special case bolted on: everything past this module
sees a `Raster` and cannot tell which it came from.

The two differ in exactly three ways, all handled here:

    pages       a PDF has many, an image has one.
    scale       a PDF states its physical size, so px-per-inch is derived. An image does not,
                so the DPI is DECLARED and travels with the document. It matters less than it
                looks: templates and radius bands are measured from the selection, so only
                the candidate size band and the readouts in feet depend on it.
    resampling  a PDF is rendered at whatever DPI is asked for. An image is NOT resampled --
                its pixels already are the drawing, and rendering a 1800 px scan at "300 DPI"
                would upscale it to 5625 px and invent nothing.

Rasterization happens once per (pdf_hash, page, dpi, clip) and is cached. Nothing downstream
of this module ever sees the PDF. Two artifacts come out of here and must not be confused:

    Raster.gray   greyscale, single DPI (default 300, where a duplex receptacle glyph is
                  0.092 in ~ 28 px). The ONLY input to detection. The DPI is carried on the
                  Raster so a run is reproducible.
    DZI tiles     RGB, multi-resolution, for the viewer. Never scored against.

A sheet is 36 x 24 in. At 300 DPI that is 10800 x 7200 = 77.7 MP, and 138 MP at 400 DPI,
so the viewer pyramid is built band by band rather than held whole. Measured on this file:
a full-page greyscale render at 300 DPI costs 7.2 s, a single tile-row band costs 0.09 s.

May import pymupdf.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import shutil
from contextlib import nullcontext
from pathlib import Path
from typing import Callable

import cv2
import numpy as np
import pymupdf

from takeoff import spaces
from takeoff.schema import InkLayers, Raster
from takeoff.spaces import PageSpace, Point, Rect

__all__ = ["InkLayers", "Raster", "render", "build_dzi", "dzi_dir", "dzi_is_built"]

DETECTION_DPI = 300
VIEWER_DPI = 300
DZI_TILE = 512
DZI_OVERLAP = 1
DZI_FORMAT = "png"  # line art: PNG stays crisp where JPEG rings on hairlines

CACHE_ROOT = Path("cache")

# What counts as an image source. PyMuPDF can open these too, but it maps them at 96 DPI and
# would resample; reading the pixels directly is lossless, faster, and says what it means.
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"})

# What an image scan is assumed to be, when nothing better is known. Overridable per document.
DEFAULT_IMAGE_DPI = 300


def is_image(source_path: str | Path) -> bool:
    return Path(source_path).suffix.lower() in IMAGE_SUFFIXES


def has_text_layer(source_path: str | Path) -> bool:
    """Whether captions can be read off this source at all. False for every scan."""
    return not is_image(source_path)


def source_hash(source_path: str | Path) -> str:
    """First 12 hex of the file digest: the cache is keyed on content, not filename."""
    h = hashlib.sha256()
    with open(source_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:12]


# The old name, kept because it reads correctly at PDF call sites and the cache paths built
# from it must not change.
pdf_hash = source_hash


def page_count(source_path: str | Path) -> int:
    if is_image(source_path):
        return 1
    with pymupdf.open(source_path) as doc:
        return doc.page_count


def _image_gray(source_path: str | Path) -> np.ndarray:
    gray = cv2.imread(str(source_path), cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"could not read {source_path} as an image")
    return gray


def page_space(source_path: str | Path, page_index: int, dpi: int = DEFAULT_IMAGE_DPI) -> PageSpace:
    """The page's rotation and size. An image has no rotation, so this is the identity."""
    if is_image(source_path):
        h, w = _image_gray(source_path).shape
        size_pt = (w / dpi * spaces.PT_PER_INCH, h / dpi * spaces.PT_PER_INCH)
        identity = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        return PageSpace(
            page_index=0,
            rotation=0,
            page_size_pt=size_pt,
            sheet_size_pt=size_pt,
            rot=identity,
            derot=identity,
        )
    with pymupdf.open(source_path) as doc:
        return PageSpace.from_page(doc[page_index])


def _pixmap_to_gray(pm: pymupdf.Pixmap) -> np.ndarray:
    """Wrap a greyscale pixmap as numpy.

    `stride` is not always `width` -- rows can be padded -- so slice rather than assume.
    """
    buf = np.frombuffer(pm.samples, dtype=np.uint8)
    return buf.reshape(pm.height, pm.stride)[:, : pm.width].copy()


def render(
    pdf_path: str | Path,
    page_index: int,
    dpi: int = DETECTION_DPI,
    clip_sheet_pt: Rect | None = None,
    use_cache: bool = True,
) -> Raster:
    """Render one page, or a clip of it, to greyscale.

    `clip_sheet_pt` is in sheet_pt -- the rotated, visible space that `page.rect` uses and
    that a human sees. It is NOT page_pt. See takeoff.spaces.
    """
    if is_image(pdf_path):
        # An image is already the drawing. Return its own pixels and let `dpi` mean what the
        # scan is DECLARED to be, so px -> inches downstream stays meaningful. Resampling it
        # to some nominal DPI would cost memory and add nothing.
        gray = _image_gray(pdf_path)
        if clip_sheet_pt is not None:
            x0, y0, x1, y1 = spaces.sheet_rect_to_px(clip_sheet_pt, dpi)
            gray = gray[int(y0) : int(y1), int(x0) : int(x1)]
            origin = (clip_sheet_pt[0], clip_sheet_pt[1])
        else:
            origin = (0.0, 0.0)
        return Raster(gray=gray, dpi=dpi, origin_sheet_pt=origin, page_index=0)

    key = f"p{page_index:03d}_d{dpi}"
    if clip_sheet_pt is not None:
        key += "_c" + "_".join(f"{v:.1f}" for v in clip_sheet_pt)
    cache_file = CACHE_ROOT / "raster" / pdf_hash(pdf_path) / f"{key}.png"
    meta_file = cache_file.with_suffix(".txt")

    if use_cache and cache_file.exists() and meta_file.exists():
        gray = cv2.imread(str(cache_file), cv2.IMREAD_GRAYSCALE)
        if gray is not None:
            ox, oy = (float(v) for v in meta_file.read_text().split())
            return Raster(gray=gray, dpi=dpi, origin_sheet_pt=(ox, oy), page_index=page_index)

    zoom = dpi / spaces.PT_PER_INCH
    with pymupdf.open(pdf_path) as doc:
        page = doc[page_index]
        clip = pymupdf.Rect(*clip_sheet_pt) if clip_sheet_pt is not None else None
        pm = page.get_pixmap(
            matrix=pymupdf.Matrix(zoom, zoom),
            clip=clip,
            colorspace=pymupdf.csGRAY,
            alpha=False,
        )
        gray = _pixmap_to_gray(pm)
        # pm.x / pm.y are the pixmap's integer offset in scaled sheet space. Taking the
        # origin from there rather than from the requested clip keeps px -> sheet_pt exact
        # after PyMuPDF snaps the clip to whole pixels.
        origin = (pm.x / zoom, pm.y / zoom)

    if use_cache:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(cache_file), gray)
        meta_file.write_text(f"{origin[0]} {origin[1]}")

    return Raster(gray=gray, dpi=dpi, origin_sheet_pt=origin, page_index=page_index)


# --------------------------------------------------------------------------- DZI pyramid


def levels_for(width: int, height: int) -> list[tuple[int, int, int]]:
    """(level, width, height) for every Deep Zoom level, coarsest first."""
    max_level = max(1, math.ceil(math.log2(max(width, height))))
    out = []
    for level in range(max_level + 1):
        scale = 2 ** (max_level - level)
        out.append((level, max(1, math.ceil(width / scale)), max(1, math.ceil(height / scale))))
    return out


def _descriptor(width: int, height: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<Image xmlns="http://schemas.microsoft.com/deepzoom/2008"\n'
        f'       Format="{DZI_FORMAT}" Overlap="{DZI_OVERLAP}" TileSize="{DZI_TILE}">\n'
        f'  <Size Width="{width}" Height="{height}"/>\n'
        "</Image>\n"
    )


def dzi_dir(pdf_path: str | Path, page_index: int) -> Path:
    return CACHE_ROOT / "dzi" / pdf_hash(pdf_path) / f"p{page_index:03d}"


def dzi_is_built(pdf_path: str | Path, page_index: int) -> bool:
    return (dzi_dir(pdf_path, page_index) / "COMPLETE").exists()


def _render_band(
    page: pymupdf.Page, level_w: int, level_h: int, y0: int, y1: int, sheet: Point
) -> np.ndarray:
    """Render pixel rows [y0, y1) of a pyramid level, full width, as RGB."""
    zx, zy = level_w / sheet[0], level_h / sheet[1]
    clip = pymupdf.Rect(0, y0 / zy, sheet[0], y1 / zy)
    pm = page.get_pixmap(matrix=pymupdf.Matrix(zx, zy), clip=clip, alpha=False)
    buf = np.frombuffer(pm.samples, np.uint8).reshape(pm.height, pm.stride)
    band = buf[:, : pm.width * 3].reshape(pm.height, pm.width, 3)
    want_h, want_w = y1 - y0, level_w
    if band.shape[0] != want_h or band.shape[1] != want_w:
        # PyMuPDF snaps the clip to whole pixels, so a band can land +-1 px off. Correcting
        # here keeps tile boundaries exact and prevents visible seams in the viewer.
        band = cv2.resize(band, (want_w, want_h), interpolation=cv2.INTER_AREA)
    return band


def build_dzi(
    pdf_path: str | Path,
    page_index: int,
    dpi: int = VIEWER_DPI,
    force: bool = False,
    progress: Callable[[float, str], None] | None = None,
) -> Path:
    """Build the Deep Zoom pyramid for one page. Returns the directory holding it.

    Built band by band: a tile row is 514 px tall at any level, so peak memory is bounded by
    the level width regardless of how large the sheet is. The COMPLETE marker is written
    last, so an interrupted build is rebuilt rather than served half-finished.
    """
    out = dzi_dir(pdf_path, page_index)
    if (out / "COMPLETE").exists() and not force:
        return out
    if out.exists():
        shutil.rmtree(out)

    files_dir = out / "sheet_files"
    files_dir.mkdir(parents=True, exist_ok=True)

    # An image is already the drawing: its own pixels are level zero. Rendering it at some
    # nominal DPI would upscale a 4600 px scan to 14375 and invent nothing.
    image = _image_gray(pdf_path) if is_image(pdf_path) else None

    with nullcontext(None) if image is not None else pymupdf.open(pdf_path) as doc:
        if image is not None:
            page = sheet = None
            full_h, full_w = image.shape
        else:
            page = doc[page_index]
            sheet = (page.rect.width, page.rect.height)
            full_w = int(round(spaces.pt_to_px(sheet[0], dpi)))
            full_h = int(round(spaces.pt_to_px(sheet[1], dpi)))
        (out / "sheet.dzi").write_text(_descriptor(full_w, full_h))

        levels = levels_for(full_w, full_h)
        total_rows = sum(math.ceil(h / DZI_TILE) for _, _, h in levels)
        done_rows = 0

        for level, lw, lh in levels:
            level_dir = files_dir / str(level)
            level_dir.mkdir(exist_ok=True)
            cols, rows = math.ceil(lw / DZI_TILE), math.ceil(lh / DZI_TILE)
            # One resize per level rather than per band: a level is at most the source size,
            # and rescaling the same array once per tile row would be waste.
            level_image = (
                cv2.cvtColor(
                    cv2.resize(image, (lw, lh), interpolation=cv2.INTER_AREA),
                    cv2.COLOR_GRAY2RGB,
                )
                if image is not None
                else None
            )
            for row in range(rows):
                y0 = max(row * DZI_TILE - DZI_OVERLAP, 0)
                y1 = min((row + 1) * DZI_TILE + DZI_OVERLAP, lh)
                band = (
                    level_image[y0:y1]
                    if level_image is not None
                    else _render_band(page, lw, lh, y0, y1, sheet)
                )
                for col in range(cols):
                    x0 = max(col * DZI_TILE - DZI_OVERLAP, 0)
                    x1 = min((col + 1) * DZI_TILE + DZI_OVERLAP, lw)
                    cv2.imwrite(
                        str(level_dir / f"{col}_{row}.{DZI_FORMAT}"),
                        cv2.cvtColor(band[:, x0:x1], cv2.COLOR_RGB2BGR),
                    )
                done_rows += 1
                if progress:
                    progress(done_rows / total_rows, f"level {level} of {levels[-1][0]}")

    (out / "COMPLETE").write_text(f"{full_w} {full_h}\n")
    return out


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Rasterize a sheet or build its DZI pyramid.")
    ap.add_argument("--pdf", default="Skanksa.pdf")
    ap.add_argument("--page", type=int, required=True, help="1-based sheet number")
    ap.add_argument("--dpi", type=int, default=DETECTION_DPI)
    ap.add_argument("--dzi", action="store_true", help="build the viewer tile pyramid")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    index = args.page - 1
    if args.dzi:
        out = build_dzi(
            args.pdf,
            index,
            force=args.force,
            progress=lambda f, m: print(f"\r  {f * 100:5.1f}%  {m}    ", end="", flush=True),
        )
        print(f"\n  {out}")
        return

    raster = render(args.pdf, index, dpi=args.dpi, use_cache=not args.force)
    w, h = raster.size_px
    print(f"  page {args.page}  {w} x {h} px @ {raster.dpi} DPI")
    print(f"  origin_sheet_pt {raster.origin_sheet_pt}")
    print("  ink and component stats: python -m takeoff.candidates --page", args.page)


if __name__ == "__main__":
    _cli()
