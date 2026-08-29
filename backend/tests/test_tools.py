"""The bench scripts in backend/tools/ are never imported by anything.

Nothing else in this suite would notice a syntax error in one, so a broken probe
ships and is discovered by whoever runs it on the Pi with the hardware in front
of them — which is exactly the moment it is least welcome. Compiling them is the
cheapest possible guard and needs no dependencies.
"""
import py_compile
from pathlib import Path

import pytest

TOOLS = sorted((Path(__file__).resolve().parents[1] / "tools").glob("*.py"))


def test_there_are_tools_to_check():
    """Guards the glob itself: an empty list would make the parametrised test
    below vacuously pass forever."""
    assert TOOLS


@pytest.mark.parametrize("path", TOOLS, ids=lambda p: p.name)
def test_tool_script_compiles(path, tmp_path):
    try:
        py_compile.compile(str(path), cfile=str(tmp_path / "out.pyc"),
                           doraise=True)
    except py_compile.PyCompileError as e:
        pytest.fail(f"{path.name} does not compile:\n{e}")
