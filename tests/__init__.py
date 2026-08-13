"""Marks `tests` as a package.

Not cosmetic. Ten test modules import siblings as `tests.live.conftest` and
`tests.live.test_recovery`. Without this file `tests` is only importable when
the repository root happens to be on sys.path -- true on a developer machine
via the editable install, false on a clean CI checkout, where pytest's prepend
import mode walks up only as far as the last directory containing an
`__init__.py` and inserts `tests/` rather than the repository root.

With it, pytest walks past `tests/live/__init__.py` and this file to the
repository root, inserts that, and `tests.live.*` resolves the same way
everywhere.
"""
