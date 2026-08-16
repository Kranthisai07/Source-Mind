for i in range(10):
    print(f"MARKER-{i}-ALEMBIC-SCRIPT-ABOUT-TO-RUN", flush=True)

print("=== DEBUG ALEMBIC SCRIPT STARTED ===", flush=True)
import sys
sys.stdout.flush()
sys.stderr.flush()
print(f"Python: {sys.version}", flush=True)
print(f"Executable: {sys.executable}", flush=True)
print(f"CWD marker — argv: {sys.argv}", flush=True)
sys.stdout.flush()
print("About to import psycopg2...", flush=True)
import time
import psycopg2
print("psycopg2 imported OK", flush=True)
sys.stdout.flush()
print("About to import sourcemind.core.config...", flush=True)
from sourcemind.core.config import get_settings
print("config imported OK", flush=True)
sys.stdout.flush()
print("About to call get_settings()...", flush=True)
settings = get_settings()
print("get_settings() returned OK", flush=True)
sys.stdout.flush()

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
print(f"[psycopg2] Testing connection to: {safe}", flush=True)
print(f"[psycopg2] needs_ssl computed as: {needs_ssl}", flush=True)
sys.stdout.flush()

start = time.time()
try:
    connect_kwargs = {"connect_timeout": 10}
    if needs_ssl:
        connect_kwargs["sslmode"] = "require"
    print(f"[psycopg2] calling psycopg2.connect with {connect_kwargs}...", flush=True)
    sys.stdout.flush()
    conn = psycopg2.connect(url, **connect_kwargs)
    print(f"[psycopg2] CONNECTED in {time.time()-start:.2f}s", flush=True)
    cur = conn.cursor()
    cur.execute("SELECT version()")
    print(f"[psycopg2] VERSION: {cur.fetchone()[0]}", flush=True)
    conn.close()
except Exception as e:
    print(f"[psycopg2] FAILED after {time.time()-start:.2f}s: {type(e).__name__}: {e}", flush=True)

sys.stdout.flush()
sys.stderr.flush()

for i in range(10):
    print(f"MARKER-{i}-ALEMBIC-SCRIPT-COMPLETE", flush=True)

sys.stdout.flush()
sys.stderr.flush()
