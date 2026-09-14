"""Build the eight-stock public attribution demonstration's minimal data pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


EXPECTED_SYMBOLS = [
    "000001", "000009", "000011", "000428",
    "301092", "600000", "600367", "688309",
]
BAR_FIELDS = ("date", "session", "bar", "open", "high", "low", "close", "volume")
THRESHOLD = -0.05
BOUNDARY_TOLERANCE = 1e-12


def check_finite(value: object, location: str = "dataset") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            check_finite(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            check_finite(item, f"{location}[{index}]")
    elif isinstance(value, (float, int)) and not math.isfinite(value):
        raise ValueError(f"Nonfinite number: {location}")


def bar_seconds(row: dict) -> int:
    return (34200 if row["session"] == 0 else 46800) + (row["bar"] + 1) * 300


def scan_crossings(dataset: dict) -> dict[str, list[dict]]:
    events: dict[str, list[dict]] = {"drawdown": [], "day_return": []}
    for symbol in dataset["symbols"]:
        for date in dataset["dates"]:
            date_key = str(date)
            previous_close = dataset["daily"][symbol][date_key]["prev_close"]
            highest_closed_price = None
            was_below = {"drawdown": False, "day_return": False}
            for row in dataset["bars"][symbol][date_key]:
                close = row["close"]
                changes = {
                    "drawdown": close / highest_closed_price - 1
                    if highest_closed_price is not None else 0.0,
                    "day_return": close / previous_close - 1,
                }
                for basis, change in changes.items():
                    below = change < THRESHOLD - BOUNDARY_TOLERANCE
                    if below and not was_below[basis]:
                        seconds = bar_seconds(row)
                        events[basis].append({
                            "symbol": symbol,
                            "date": date,
                            "seconds": seconds,
                            "time": f"{seconds // 3600:02}:{seconds % 3600 // 60:02}",
                            "change": change,
                            "close": close,
                        })
                    was_below[basis] = below
                # Only previously completed bars form the next observation's peak.
                highest_closed_price = max(highest_closed_price or close, close)
    for rows in events.values():
        rows.sort(key=lambda row: (row["date"], row["seconds"], row["symbol"]))
    return events


def prepare(source: Path, output_directory: Path) -> dict:
    source_data = json.loads(source.read_text(encoding="utf-8"))
    if source_data["symbols"] != EXPECTED_SYMBOLS:
        raise ValueError("Expected the authorized eight-stock replay pack")
    dates = source_data["dates"]
    if len(dates) != 23 or dates != sorted(set(dates)):
        raise ValueError("Expected 23 unique, chronologically sorted trading dates")

    result = {
        "schema_version": 1,
        "metadata": {
            "mode": "public_historical_attribution_demonstration",
            "prices": "historical_five_minute_bars",
            "causes": "fictional_examples_generated_by_browser",
            "probabilities": "manually_assigned_examples_not_estimated_posteriors",
            "timezone": "Asia/Shanghai",
            "bar_timestamp": "completed_bar_end",
            "aws_dependency": False,
        },
        "symbols": list(EXPECTED_SYMBOLS),
        "dates": list(dates),
        "bars": {},
        "daily": {},
    }
    expected_order = [(session, bar) for session in range(2) for bar in range(24)]
    for symbol in EXPECTED_SYMBOLS:
        result["bars"][symbol] = {}
        result["daily"][symbol] = {}
        for date in dates:
            date_key = str(date)
            bars = [
                {key: row[key] for key in BAR_FIELDS}
                for row in source_data["bars"][symbol][date_key]
            ]
            if [(row["session"], row["bar"]) for row in bars] != expected_order:
                raise ValueError(f"Missing or unordered bars: {symbol}, {date}")
            for row in bars:
                if row["date"] != date:
                    raise ValueError(f"Inconsistent bar date: {symbol}, {date}")
                if min(row[key] for key in ("open", "high", "low", "close")) <= 0:
                    raise ValueError(f"Nonpositive price: {symbol}, {date}")
                if row["volume"] < 0:
                    raise ValueError(f"Negative volume: {symbol}, {date}")
                if not (row["low"] <= min(row["open"], row["close"]) <=
                        max(row["open"], row["close"]) <= row["high"]):
                    raise ValueError(f"Invalid OHLC order: {symbol}, {date}")
            previous_close = source_data["daily"][symbol][date_key]["prev_close"]
            if previous_close <= 0:
                raise ValueError(f"Invalid reference close: {symbol}, {date}")
            result["bars"][symbol][date_key] = bars
            # End-of-day price and volume summaries are not required by this demo.
            result["daily"][symbol][date_key] = {
                "date": str(date),
                "prev_close": previous_close,
            }
    check_finite(result)
    crossings = scan_crossings(result)
    payload = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    payload_bytes = payload.encode("utf-8")
    counts = {
        "stocks": len(result["symbols"]),
        "trading_dates": len(dates),
        "stock_days": sum(len(days) for days in result["bars"].values()),
        "bars": sum(len(rows) for days in result["bars"].values() for rows in days.values()),
        "daily_reference_rows": sum(len(days) for days in result["daily"].values()),
    }
    if list(counts.values()) != [8, 23, 184, 8832, 184]:
        raise ValueError(f"Unexpected pack dimensions: {counts}")
    manifest = {
        "schema_version": 1,
        "file": "replay.json",
        "sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "bytes": len(payload_bytes),
        **counts,
        "date_range": [dates[0], dates[-1]],
        "bar_interval_seconds": 300,
        "timezone": "Asia/Shanghai",
        "crossing_threshold": THRESHOLD,
        "boundary_tolerance": BOUNDARY_TOLERANCE,
        "crossings": {
            basis: {"count": len(rows), "first": rows[0] if rows else None}
            for basis, rows in crossings.items()
        },
        "starter_case": {
            "date": 20260727, "symbol": "000011", "basis": "drawdown",
            "start_seconds": 48900, "event_seconds": 49200,
        },
        "genuine_historical_prices": True,
        "fictional_causes_and_probabilities": True,
        "model_packages_included": False,
        "credentials_included": False,
        "aws_dependency": False,
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    (output_directory / "replay.json").write_bytes(payload_bytes)
    (output_directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8", newline="\n",
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Authorized eight-stock source replay JSON")
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parents[1] / "data")
    arguments = parser.parse_args()
    manifest = prepare(arguments.source, arguments.output)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
