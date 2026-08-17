"""CI guard for Bug 1: the built package must actually contain files.

Run inside (or against) a built image:

    python scripts/check_wheel.py

WHY THIS EXISTS
---------------
The Dockerfile once ran `pip install -e "."` before `COPY sourcemind/`.
hatchling's wheel target is packages = ["sourcemind"], so with that directory
absent the build did **not** fail — it emitted a valid 1.5 KB wheel
containing zero package files. Nothing in the build output indicated a
problem.

That was survivable only because the production stage also copies the source
and sets PYTHONPATH=/app, so imports resolved from the filesystem regardless.
Switching to a non-editable `pip install "."` would have shipped an image
that installs nothing and dies at `import sourcemind`.

A build that silently produces an empty artifact is exactly the class of
failure worth a CI assertion: it cannot be caught by unit tests, and the
build's own exit code is 0.
"""

from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, distribution

PACKAGE = "sourcemind-api"
IMPORT_NAME = "sourcemind"
MIN_MODULES = 20  # the package has ~85 files; 20 is a generous floor


def main() -> int:
    # 1. The distribution must be installed at all.
    try:
        dist = distribution(PACKAGE)
    except PackageNotFoundError:
        print(f"FAIL: {PACKAGE} is not installed", file=sys.stderr)
        return 1
    print(f"  {PACKAGE} version {dist.version} is installed")

    # 2. The import must resolve to a real package with submodules.
    try:
        import sourcemind
    except ImportError as exc:
        print(f"FAIL: cannot import {IMPORT_NAME}: {exc}", file=sys.stderr)
        return 1

    location = getattr(sourcemind, "__file__", None)
    if not location:
        print(f"FAIL: {IMPORT_NAME} has no __file__ — namespace package?", file=sys.stderr)
        return 1
    print(f"  {IMPORT_NAME} resolves to {location}")

    # 3. The package must contain a plausible number of modules. An empty
    #    wheel plus a stray __init__.py would satisfy steps 1 and 2.
    import pathlib

    root = pathlib.Path(location).parent
    modules = [p for p in root.rglob("*.py") if "__pycache__" not in str(p)]
    print(f"  {len(modules)} python modules found under {root}")
    if len(modules) < MIN_MODULES:
        print(
            f"FAIL: only {len(modules)} modules — expected at least {MIN_MODULES}. "
            "The package was likely built before its source was copied, "
            "producing an empty wheel.",
            file=sys.stderr,
        )
        return 1

    # 4. The entry point the server actually loads must import.
    try:
        from sourcemind.main import app
    except Exception as exc:
        print(f"FAIL: cannot import sourcemind.main:app: {exc!r}", file=sys.stderr)
        return 1
    print(f"  sourcemind.main:app imports OK ({len(app.routes)} routes)")

    print("PASS: installed package contains real files and imports cleanly")
    return 0


if __name__ == "__main__":
    sys.exit(main())
