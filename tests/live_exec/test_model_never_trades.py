"""The signal model grades; it never filters a trade (owner, 2026-10-13).

The grader (live/learning.py) and the model (deltabt/signal_model.py, sklearn)
must be unreachable from anything that decides or places an order: all of
app/, every live/ module but the grader itself, and the bot's own start-up in
live/__main__.py (which may import the grader only inside the
`if __name__ == "__main__"` dispatch, for the label/grade subcommands).
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
GRADER = {"live.learning", "deltabt.signal_model", "sklearn"}


def _imports(tree) -> set[str]:
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            out.add(mod)
            out |= {f"{mod}.{a.name}" for a in node.names}
    return out


def _hits(names: set[str]) -> set[str]:
    return {n for n in names if any(n == g or n.startswith(g + ".") for g in GRADER)}


def _trading_files():
    for p in sorted((ROOT / "app").rglob("*.py")):
        yield p
    for p in sorted((ROOT / "live").glob("*.py")):
        if p.name not in ("learning.py", "__main__.py"):
            yield p


def test_no_trading_module_imports_the_grader_or_the_model():
    bad = {str(p.relative_to(ROOT)): sorted(h) for p in _trading_files()
           if (h := _hits(_imports(ast.parse(p.read_text()))))}
    assert not bad, f"the model/grader is reachable from trading code: {bad}"


def test_the_bot_entry_point_loads_the_grader_only_for_its_subcommands():
    tree = ast.parse((ROOT / "live/__main__.py").read_text())
    for node in tree.body:
        is_main_guard = (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                         and getattr(node.test.left, "id", "") == "__name__")
        if is_main_guard:
            continue
        assert not _hits(_imports(node)), (
            f"live/__main__.py line {node.lineno} reaches the grader outside the "
            f"subcommand dispatch")


def test_the_recorder_writes_and_reads_nothing_back():
    """app/learning/recorder.py's only output is a repository write."""
    src = (ROOT / "app/learning/recorder.py").read_text()
    tree = ast.parse(src)
    assert not _hits(_imports(tree))
    calls = {n.func.attr for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and isinstance(n.func.value, ast.Name) and n.func.value.id == "repo"}
    assert calls == {"record_signal_features"}, calls
