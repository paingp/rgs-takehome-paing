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

# Part of the install check: `pytest -m smoke`, which is what install.ps1 runs. This one proves
# the package imports at all and that the raster-only boundary still holds.
pytestmark = pytest.mark.smoke

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
    "regions",
]


def _is_type_checking_guard(node: ast.stmt) -> bool:
    """`if TYPE_CHECKING:` / `if typing.TYPE_CHECKING:`"""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if isinstance(test, ast.Name):
        return test.id == "TYPE_CHECKING"
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _imports(module: str) -> set[str]:
    """Top-level package names imported by takeoff/<module>.py at RUNTIME.

    Relative imports are returned as `takeoff.<name>` so the walk can follow them.

    Imports under `if TYPE_CHECKING:` are skipped, and that exception is what lets a
    detection module import `takeoff.spaces` for its px/inch arithmetic. spaces.py is on the
    allowed list because coordinate conversion is conceptually about the PDF's rotation, but
    it only needs pymupdf for an annotation -- so it costs nothing at runtime, and the guard
    would otherwise reject `candidates.py -> spaces.py` even though no PDF is reachable.
    The exception is safe by construction: a name imported only under TYPE_CHECKING raises
    NameError the moment anything tries to use it.
    """
    path = PACKAGE / f"{module}.py"
    return _imports_from_source(path.read_text(encoding="utf-8"), str(path))


def _imports_from_source(source: str, filename: str = "<test>") -> set[str]:
    tree = ast.parse(source, filename=filename)
    found: set[str] = set()

    # Everything lexically inside a TYPE_CHECKING guard, so the walk below can ignore it.
    type_only: set[ast.AST] = set()
    for node in ast.walk(tree):
        if _is_type_checking_guard(node):
            for inner in ast.walk(node):
                type_only.add(inner)

    for node in ast.walk(tree):
        if node in type_only:
            continue
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


def test_spaces_is_runtime_pure_so_detectors_may_import_it() -> None:
    """spaces.py is allowed pymupdf but does not actually need it at runtime.

    Detection modules need its px/inch arithmetic. This asserts that importing it does not
    smuggle a PDF handle into the detection half of the codebase.
    """
    external, _ = _reachable("spaces")
    assert not external & FORBIDDEN, f"spaces.py reaches {sorted(external & FORBIDDEN)}"


TYPE_CHECKING_FORM = """
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pymupdf
"""

RUNTIME_FORMS = [
    "import pymupdf",
    "from pymupdf import Rect",
    "def f():\n    import pymupdf\n",
    "try:\n    import pymupdf\nexcept ImportError:\n    pymupdf = None\n",
    "if True:\n    import pymupdf\n",
]


def test_type_checking_exception_is_narrow() -> None:
    """Only the annotation form is excused; a real runtime import is still caught."""
    assert "pymupdf" not in _imports_from_source(TYPE_CHECKING_FORM)
    for runtime_form in RUNTIME_FORMS:
        assert "pymupdf" in _imports_from_source(runtime_form), runtime_form
