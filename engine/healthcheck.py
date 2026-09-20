"""Bot liveness AND market freshness; never accesses an exchange."""

import os
from pathlib import Path
import time

from .models import HOUR_MS
from .storage import read_status


def main():
    hb = read_status(Path(os.getenv("DATA_DIR", "data")) / "paper.sqlite3")["heartbeat"]
    now = int(time.time() * 1000)
    last = hb.get("last_candle_open_ms")
    ok = (
        hb.get("status") == "running"
        and 0 <= now - hb.get("ts_ms", 0) <= 900_000
        and last is not None
        and 0 <= now - last - HOUR_MS <= HOUR_MS + 900_000
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
