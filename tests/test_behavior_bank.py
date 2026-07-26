from pathlib import Path

import pandas as pd

from behavior_bank import extract_behavior, load_ohlcv, write_database


def test_extract_and_write_behavior_bank(tmp_path: Path) -> None:
    timestamps = pd.date_range("2025-01-01", periods=300, freq="5min", tz="UTC")
    close = pd.Series(range(300), index=timestamps, dtype=float) + 100
    frame = pd.DataFrame(
        {
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 10.0,
        },
        index=timestamps,
    )
    extracted = extract_behavior("TEST", frame.resample("1h").agg({"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna())
    assert extracted["trends"]
    destination = tmp_path / "behavior.db"
    write_database(destination, "TEST", extracted, [])
    assert destination.exists()


def test_load_ohlcv_accepts_parquet(tmp_path: Path) -> None:
    timestamps = pd.date_range("2025-01-01", periods=20, freq="5min", tz="UTC")
    source = tmp_path / "sample.parquet"
    pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": 1.0,
            "high": 2.0,
            "low": 0.5,
            "close": 1.5,
            "volume": 3.0,
        }
    ).to_parquet(source)
    assert len(load_ohlcv(source)) == 2
