import sys
from pathlib import Path

LOG_FILE = Path("/tmp/debug_alembic.log")


def log(msg):
    """Print and append to LOG_FILE so output survives log-viewer gaps."""
    print(msg, flush=True)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(str(msg) + "\n")
            f.flush()
    except Exception as exc:
        print(f"(log file write failed: {exc})", flush=True)
    sys.stdout.flush()


for i in range(10):
    log(f"MARKER-{i}-ALEMBIC-SCRIPT-ABOUT-TO-RUN")

log("=== DEBUG ALEMBIC SCRIPT STARTED ===")
log(f"Python: {sys.version}")
log(f"Executable: {sys.executable}")
log(f"CWD marker — argv: {sys.argv}")
log("About to import psycopg2...")
import time
import psycopg2
log("psycopg2 imported OK")
log("About to import sourcemind.core.config...")
from sourcemind.core.config import get_settings
log("config imported OK")
log("About to call get_settings()...")
settings = get_settings()
log("get_settings() returned OK")

# TEMPORARY diagnostic — psycopg2 path, mirroring alembic/env.py exactly.
#
# debug_db_connect.py proves asyncpg works. Alembic never uses asyncpg: it
# rewrites the URL to psycopg2 and applies its own sslmode logic, so it can
# fail where asyncpg succeeds. This reproduces that path with the same driver,
# the same URL rewriting, and the same needs_ssl computation.
#
# The repeated MARKER lines at the top exist so that partial log truncation
# cannot hide the fact that this script ran at all. Remove this file and
# restore the original startCommand once the answer is known.

url = settings.database_url.replace(
    "postgresql+asyncpg://", "postgresql://"
).replace("?ssl=require", "").replace("&ssl=require", "")

original_url = settings.database_url
needs_ssl = (
    "ssl=require" in original_url
    or ".railway.internal" not in original_url
)

safe = url.split('@')[1] if '@' in url else url
log(f"[psycopg2] Testing connection to: {safe}")
log(f"[psycopg2] needs_ssl computed as: {needs_ssl}")

start = time.time()
try:
    connect_kwargs = {"connect_timeout": 10}
    if needs_ssl:
        connect_kwargs["sslmode"] = "require"
    log(f"[psycopg2] calling psycopg2.connect with {connect_kwargs}...")
    sys.stdout.flush()
    conn = psycopg2.connect(url, **connect_kwargs)
    log(f"[psycopg2] CONNECTED in {time.time()-start:.2f}s")
    cur = conn.cursor()
    cur.execute("SELECT version()")
    log(f"[psycopg2] VERSION: {cur.fetchone()[0]}")
    conn.close()
except Exception as e:
    log(f"[psycopg2] FAILED after {time.time()-start:.2f}s: {type(e).__name__}: {e}")


for i in range(10):
    log(f"MARKER-{i}-ALEMBIC-SCRIPT-COMPLETE")

