#!/usr/bin/env python3
"""Measure how long a SIGKILLed Celery worker's unacknowledged task takes to be redelivered.

Minimal app (one task, acks_late, Redis broker); no SourceMind code. Run inside WSL:
  python redelivery_probe.py <visibility_timeout> <unacked_mutex_expire|default> [runs]
Prints one line per run: seconds from the kill until a SECOND worker logs "received", plus the broker
keys that hold the unacked message before the second worker starts. Uses Redis db 12.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import redis

VT = int(sys.argv[1])
MUTEX = sys.argv[2]
RUNS = int(sys.argv[3]) if len(sys.argv) > 3 else 1
URL = os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:56379/15").rsplit("/", 1)[0] + "/12"
WORK = Path("/tmp/redelivery_probe")
WORK.mkdir(exist_ok=True)

APP = f'''
from celery import Celery
app = Celery("probe", broker="{URL}", backend="{URL}")
opts = {{"visibility_timeout": {VT}}}
{"opts['unacked_mutex_expire'] = " + MUTEX if MUTEX != "default" else ""}
app.conf.broker_transport_options = opts
app.conf.task_acks_late = True
app.conf.worker_prefetch_multiplier = 1

@app.task(name="probe.hang", acks_late=True)
def hang():
    import time
    open("{WORK}/started", "a").write("x")
    time.sleep(600)
'''
(WORK / "probe_app.py").write_text(APP)


def start(name: str) -> subprocess.Popen:
    log = (WORK / f"{name}.log").open("w")
    return subprocess.Popen(
        [sys.executable, "-m", "celery", "-A", "probe_app:app", "worker", "-c", "1", "-P", "prefork",
         "-l", "INFO", "--without-gossip", "--without-mingle", "--without-heartbeat"],
        cwd=WORK, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
    )


def wait_for(path: Path, needle: str, timeout: float) -> float | None:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if path.exists() and needle in path.read_text(errors="replace"):
            return time.monotonic()
        time.sleep(0.2)
    return None


r = redis.Redis.from_url(URL, decode_responses=True)
for run in range(1, RUNS + 1):
    r.flushdb()
    for f in WORK.glob("*.log"):
        f.unlink()
    (WORK / "started").unlink(missing_ok=True)
    w1 = start("w1")
    assert wait_for(WORK / "w1.log", "ready.", 60), "w1 not ready"
    sys.path.insert(0, str(WORK))
    from probe_app import app  # noqa: E402

    app.send_task("probe.hang")
    t_run = wait_for(WORK / "started", "x", 30)
    assert t_run, "task never started"
    os.killpg(w1.pid, signal.SIGKILL)
    w1.wait()
    t_kill = time.monotonic()
    unacked = {}
    for k in r.keys("*unacked*"):
        t = r.type(k)
        unacked[k] = (t, r.hlen(k) if t == "hash" else r.zcard(k) if t == "zset" else r.llen(k))
    time.sleep(float(os.environ.get("GAP", "0")))  # time with NO worker alive after the kill
    t_w2 = time.monotonic()
    w2 = start("w2")
    got = wait_for(WORK / "w2.log", "Task probe.hang[", 400)
    os.killpg(w2.pid, signal.SIGKILL)
    w2.wait()
    delay = None if got is None else round(got - t_kill, 1)
    print(f"vt={VT}s mutex={MUTEX} run={run}: redelivered {delay}s after the kill "
          f"(None = not within 400 s), {None if got is None else round(got - t_w2, 1)}s after the "
          f"replacement worker was started; unacked keys after kill: {unacked}")
    sys.modules.pop("probe_app", None)
