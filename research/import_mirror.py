"""Download only pinned public DATA blobs through gh; never execute mirror code.

These are third-party snapshots, not authenticated exchange archives. The
manifest says so. Raw data stays outside Git. Do not interpret checksums as
verification of the publisher's claim that the prices are exchange originals.
"""

import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess

import pandas as pd

from engine.models import HOUR_MS
from research.data import read_candles, sha256_file

SOURCES = {
    "binance": {
        "repository": "SpaciousAbhi/binance-futures-backtest-research",
        "blob": "112e4a1f87db518fac0566220641f6ab27dc1cad",
        "commit": "4388b2cc2b7b5ca7d9cb81a29b39a14ca940b4a1",
        "path": "data/processed/BTCUSDT_1h_processed.csv",
        "sha256": "bf4e392e379311e54da8c2b2a4ab50446769a38cfc2e6d1b22e4a992a73941a3",
        "claimed_exchange": "Binance USD-M perpetual futures",
    },
    "bybit": {
        "repository": "mestoness/btc-eth-candles-history",
        "blob": "5c552f546d4e5fabb7be756d855db8f879059731",
        "commit": "df3095a22122bb52eef77673fce5879d80193cd2",
        "path": "BTCUSDT_60.csv",
        "sha256": "1991ee846691d76266a7ad1a771660395b37f41a8329b8b1c402ae154bf80db4",
        "claimed_exchange": "Bybit USDT perpetual futures",
    },
}


def import_snapshot(source, output_dir):
    spec = SOURCES[source]
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    raw = output_dir / f"{source}_BTCUSDT_1h_mirror.csv"
    if not raw.exists():
        response = subprocess.run(
            ["gh", "api", f"repos/{spec['repository']}/git/blobs/{spec['blob']}"],
            check=True,
            capture_output=True,
            timeout=120,
        )
        payload = json.loads(response.stdout)
        content = base64.b64decode(payload["content"])
        if hashlib.sha256(content).hexdigest() != spec["sha256"]:
            raise ValueError("Downloaded data hash mismatch")
        raw.write_bytes(content)
    if sha256_file(raw) != spec["sha256"]:
        raise ValueError("Local mirror hash mismatch; do not silently refresh the pinned dataset")
    data = pd.read_csv(raw)
    time_column = "open_time" if source == "binance" else "timestamp"
    normalized = data.rename(columns={time_column: "time", "taker_buy_base_asset_volume": "taker_buy"})
    columns = ["time", "open", "high", "low", "close", "volume"]
    if "taker_buy" in normalized:
        columns.append("taker_buy")
    output = output_dir / f"{source}_BTCUSDT_1h.csv"
    normalized[columns].to_csv(output, index=False, float_format="%.12g")
    candles = read_candles(output)
    funding_manifest = None
    if source == "binance":
        funding = data[["fundingTime", "fundingRate"]].drop_duplicates()
        if funding["fundingTime"].duplicated().any() or funding.isna().any().any():
            raise ValueError("Conflicting or missing funding rates")
        # Source is an as-of join. Count each settlement ONCE, not once per row.
        funding["time"] = (funding["fundingTime"].astype("int64") // HOUR_MS) * HOUR_MS
        if funding["time"].duplicated().any():
            raise ValueError("Multiple funding events in one hourly execution bucket")
        funding = funding.rename(columns={"fundingRate": "rate"})[["time", "rate"]].sort_values("time")
        funding = funding[(funding.time >= candles[0].time) & (funding.time <= candles[-1].time)]
        funding_path = output_dir / f"{source}_funding.csv"
        funding.to_csv(funding_path, index=False, float_format="%.14g")
        funding_manifest = {
            "path": funding_path.name,
            "sha256": sha256_file(funding_path),
            "settlements": len(funding),
            "timestamp_policy": "settlement timestamp floored to 1h; carry positions charged at candle open",
            "price_policy": "candle open used as notional price, NOT historical mark price",
        }
    manifest = {
        "schema_version": 1,
        "source_kind": "unverified_third_party_historical_mirror",
        **spec,
        "source_url": f"https://github.com/{spec['repository']}/blob/{spec['commit']}/{spec['path']}",
        "blob_api_url": f"https://api.github.com/repos/{spec['repository']}/git/blobs/{spec['blob']}",
        "normalized_path": output.name,
        "normalized_sha256": sha256_file(output),
        "rows": len(candles),
        "start_ms": candles[0].time,
        "end_ms": candles[-1].time + HOUR_MS,
        "taker_flow_present": "taker_buy" in columns,
        "funding": funding_manifest,
        "quality_checks": [
            "finite OHLCV",
            "hourly UTC alignment",
            "OHLC bounds",
            "nonnegative volume",
            "no duplicates",
            "strict hourly continuity",
            "closed candles only",
        ],
        "limitations": [
            "Not CoinEx data",
            "Original exchange checksums could not be verified",
            "Historical rates, if present, are from the same third-party mirror",
        ],
    }
    (output_dir / f"{source}_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=SOURCES, required=True)
    parser.add_argument("--output-dir", default="data/historical")
    args = parser.parse_args()
    print(json.dumps(import_snapshot(args.source, args.output_dir), indent=2))
