"""citygen is one module written across files. The Store build has no .py files to read, so Ducky's build
engine compiles the files named in the marked list of citygen/__init__.py into the module itself. These
hold that list to the files on disk: a part left out of it would be missing from every Store build."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "backend" / "citygen"
sys.path.insert(0, str(ROOT))
from backend import citygen as cg  # noqa: E402

MARK = "# ducky-build: one module"


def _future(path: Path) -> list:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return sorted(name.name for node in tree.body
                  if isinstance(node, ast.ImportFrom) and node.module == "__future__" for name in node.names)


def test_the_marked_list_names_every_part_file_once():
    on_disk = sorted(p.name for p in HERE.glob("_*.py") if not p.name.startswith("__"))
    assert sorted(cg._CG_PARTS) == on_disk and len(set(cg._CG_PARTS)) == len(cg._CG_PARTS)
    source = (HERE / "__init__.py").read_text(encoding="utf-8")
    lines = source.splitlines()
    # ... and the mark sits on that list (one top-level line of file names), nowhere else.
    listed = [node for node in ast.parse(source).body if isinstance(node, ast.Assign)
              and getattr(node.targets[0], "id", "") == "_CG_PARTS"]
    assert len(listed) == 1 and MARK in lines[listed[0].end_lineno - 1]
    assert sum(MARK in line for line in lines) == 1
    assert [element.value for element in listed[0].value.elts] == list(cg._CG_PARTS)


def test_every_part_starts_with_the_packages_own_future_lines():
    want = _future(HERE / "__init__.py")
    assert want == ["annotations"]
    for name in cg._CG_PARTS:  # compiled as one file, they share one set of them
        assert _future(HERE / name) == want, name
