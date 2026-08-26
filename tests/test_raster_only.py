"""The load-bearing architectural rule, enforced as a test rather than a review habit.

Detection modules work on numpy arrays produced by the rasterization boundary. They may
never see the PDF's vector geometry -- not directly, and not through a chain of first-party
imports. Planting `import pymupdf` in any module listed in DETECTION_MODULES must fail CI.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

PACKAGE = Path(__file__).resolve().parent.parent / "takeoff"

FORBIDDEN = {"pymupdf", "fitz"}

# May import pymupdf: they are the boundary itself, or grading-only.
ALLOWED_MODULES = {"raster", "spaces", "layout", "vector_gt"}

# May never, directly or transitively.
DETECTION_MODULES = [
    "candidates",
    "templates",
    "scoring",
    "detect",
    "doors",
    "lifecycle",
    "banding",
    "schema",
    "classes",
]


def _imports(module: str) -> set[str]:
    """Top-level package names imported by takeoff/<module>.py.

    Relative imports are returned as `takeoff.<name>` so the walk can follow them.
    """
    path = PACKAGE / f"{module}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # from . import x / from .spaces import y
                if node.module:
                    found.add(f"takeoff.{node.module.split('.')[0]}")
                else:
                    found.update(f"takeoff.{a.name}" for a in node.names)
            elif node.module:
                found.add(node.module.split(".")[0])
    return found


def _reachable(module: str) -> tuple[set[str], list[str]]:
    """All packages reachable from `module`, following first-party imports.

    Returns (external packages seen, the path walked) so a failure can name the chain.
    """
    external: set[str] = set()
    seen: set[str] = set()
    chain: list[str] = []
    stack = [module]
    while stack:
        current = stack.pop()
        if current in seen or not (PACKAGE / f"{current}.py").exists():
            continue
        seen.add(current)
        chain.append(current)
        for name in _imports(current):
            if name.startswith("takeoff."):
                stack.append(name.split(".", 1)[1])
            elif (PACKAGE / f"{name}.py").exists():
                stack.append(name)
            else:
                external.add(name)
    return external, chain


def test_module_lists_cover_the_package() -> None:
    """Every module in takeoff/ is classified, so a new one cannot slip through unchecked."""
    on_disk = {p.stem for p in PACKAGE.glob("*.py")} - {"__init__"}
    classified = ALLOWED_MODULES | set(DETECTION_MODULES)
    assert on_disk == classified, (
        f"unclassified: {sorted(on_disk - classified)}; "
        f"listed but missing: {sorted(classified - on_disk)}"
    )


@pytest.mark.parametrize("module", DETECTION_MODULES)
def test_detection_module_never_reaches_pymupdf(module: str) -> None:
    external, chain = _reachable(module)
    leaked = external & FORBIDDEN
    assert not leaked, (
        f"takeoff.{module} reaches {sorted(leaked)} via {' -> '.join(chain)}. "
        "Detection runs on the raster only; move the PDF access behind raster.py."
    )
