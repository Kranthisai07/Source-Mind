"""The autouse per-test check catches an attempt that the code under test swallowed.

Run in a subprocess so the failing inner test does not fail this one.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_a_test_that_swallows_an_external_attempt_fails_the_run(tmp_path: Path):
    inner = tmp_path / "test_inner.py"
    inner.write_text(
        textwrap.dedent(
            """
            import socket

            def test_swallows_the_error():
                try:
                    socket.create_connection(("api.openai.com", 443), timeout=1)
                except OSError:
                    pass  # the code under test hides the failure
            """
        )
    )
    api_dir = Path(__file__).resolve().parents[2]
    conftest = api_dir / "tests" / "conftest.py"
    (tmp_path / "conftest.py").write_text(
        f"import sys\nsys.path.insert(0, {str(api_dir)!r})\n"
        f"exec(compile(open({str(conftest)!r}).read(), 'conftest', 'exec'))\n"
    )
    res = subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-m", "pytest", "-o", "addopts=", "-p", "no:cacheprovider", "-q",
         str(inner)],
        cwd=tmp_path, capture_output=True, text=True, timeout=120,
    )
    out = res.stdout + res.stderr
    assert res.returncode != 0, out
    assert "unexpected external network attempt" in out, out
