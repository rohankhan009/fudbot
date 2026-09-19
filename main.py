"""
PAPIATMA - Dono bots ko ek saath launch karta hai.
Railway ek hi service me dono chalenge.
"""
import os
import subprocess
import sys
import time
import signal
import logging

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("main")

PORT = os.getenv("PORT", "8080")
os.environ["PORT"] = PORT

PROCS = []


def start(name, cmd, env=None):
    log.info(f"▶️  Starting {name}...")
    p = subprocess.Popen(cmd, env={**os.environ, **(env or {})})
    PROCS.append((name, p))
    return p


def shutdown(*_):
    log.info("🛑 Shutting down all bots...")
    for name, p in PROCS:
        if p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
    sys.exit(0)


signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)


if __name__ == "__main__":
    # 1) PAPIATMA backend (FastAPI + Admin bot) - port PORT pe bind hoga
    start("PAPIATMA-backend", [sys.executable, "server.py"])

    # 2) PAPI SMS Relay bot (long-polling, port nahi chahiye)
    start("PAPI-SMS-Relay", [sys.executable, "papi_sms_monitor.py"])

    log.info(f"✅ Both bots launched. Backend on port {PORT}")

    # Watchdog: agar koi crash ho to log karo (Railway khud restart karega)
    while True:
        time.sleep(30)
        for name, p in PROCS:
            code = p.poll()
            if code is not None:
                log.error(f"❌ {name} died with code {code}")
