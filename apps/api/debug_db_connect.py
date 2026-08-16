"""TEMPORARY diagnostic — isolate where the DB connection hangs in Railway.

Run before the migration step in railway.json's startCommand. Remove this
file and restore the original startCommand once the answer is known.
"""

import asyncio
import time

import asyncpg

from sourcemind.core.config import get_settings


async def test():
    settings = get_settings()
    url = settings.database_url.replace("postgresql+asyncpg://", "postgresql://")
    safe = url.split('@')[1] if '@' in url else url
    print(f"Testing connection to: {safe}", flush=True)

    start = time.time()
    try:
        conn = await asyncio.wait_for(asyncpg.connect(url, timeout=10), timeout=15)
        print(f"CONNECTED in {time.time()-start:.2f}s", flush=True)
        version = await conn.fetchval("SELECT version()")
        print(f"DB VERSION: {version}", flush=True)
        await conn.close()
    except asyncio.TimeoutError:
        print(f"TIMEOUT after {time.time()-start:.2f}s — connection hung", flush=True)
    except Exception as e:
        print(f"FAILED after {time.time()-start:.2f}s: {type(e).__name__}: {e}", flush=True)


asyncio.run(test())
