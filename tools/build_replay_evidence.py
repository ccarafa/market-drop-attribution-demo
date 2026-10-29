"""Build the evidence pack for the eight-stock intraday replay.

For every stock-day in data/replay.json this collects, from the private
evidence directory and the published attribution shards, the announcements,
company and industry articles, the day's macro context, the end-of-day
attribution (when the day is an attribution event) and the daily trading
diagnostics. Every item carries its publication time so the replay can tell
what was known at the moment an intraday threshold was crossed from what was
disclosed afterwards. Only titles, times and links are written.

Usage: python tools/build_replay_evidence.py --daily <parquet> --evidence <dir>
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_real_study as study  # noqa: E402
import build_attribution as attribution  # noqa: E402
from attribution_taxonomy import FAMILIES  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def load_attribution_events(symbols):
    """Map (symbol, date) -> event record and the index labels."""
    index = json.loads((ROOT / "data/attribution/index.json").read_text(encoding="utf-8"))
    wanted = {}
    for path in sorted((ROOT / "data/attribution/events").glob("2026*-[235].json")):
        shard = json.loads(path.read_text(encoding="utf-8"))
        for event in shard["events"]:
            if event["symbol"] in symbols:
                event["_news"] = shard["news"]
                wanted[(event["symbol"], int(event["date"]))] = event
    return index, wanted


def build(daily, evidence_dir, moneyflow_dir=None):
    import pyarrow.parquet as pq
    replay = json.loads((ROOT / "data/replay.json").read_text(encoding="utf-8"))
    symbols = list(replay["symbols"])
    dates = [int(d) for d in replay["dates"]]
    index, events = load_attribution_events(set(symbols))
    evidence = attribution.build_evidence(evidence_dir)
    moneyflow = attribution.load_moneyflow(moneyflow_dir)
    stock_industry = evidence["stock_industry"]
    records = pq.read_table(daily, columns=study.SOURCE_COLUMNS).to_pylist()
    rows, _, market, _ = study.compute_study(records)
    daily_rows = {(r["symbol"], r["date"]): r for r in rows if r["symbol"] in symbols}
    calendar = attribution.Calendar(sorted({r["date"] for r in rows}))
    market_by_day = {m["date"]: m for m in market}
    labels = index["feature_labels"]
    days = defaultdict(dict)
    macro_by_date = {}
    for date in dates:
        windows = calendar.windows(date)
        if windows:
            start, end = windows["pre_open"][0], windows["post_close"][1]
            macro_by_date[str(date)] = [{"id": str(n["id"]), "title": n["title"][:70], "source": n["source"], "url": n["url"],
                                         "time": attribution.iso(n["ts"]), "regulator": n["regulator"], "macro": n["macro"]}
                                        for n in evidence["news_macro"].between("all", start, end)][:40]
        for symbol in symbols:
            industry = stock_industry.get(symbol)
            annos, news_company, news_industry = [], [], []
            if windows:
                for window, (start, end) in windows.items():
                    for record in evidence["anno"].between(symbol, start, end):
                        annos.append({"id": record["id"], "title": record["title"][:70], "family": record["family"],
                                      "label": FAMILIES[record["family"]][0], "direction": record["direction"],
                                      "informational": record["informational"], "time": attribution.iso(record["ts"]), "window": window})
                    for record in evidence["news_company"].between(symbol, start, end):
                        news_company.append({"id": str(record["id"]), "title": record["title"][:70], "source": record["source"], "url": record["url"],
                                             "time": attribution.iso(record["ts"]), "window": window, "direction": record["direction"]})
                    if industry and window != "prior":
                        hits = evidence["news_industry"].between(industry, start, end)
                        for record in [h for h in hits if h["regulator"]][:12] + [h for h in hits if not h["regulator"]][:8]:
                            news_industry.append({"id": str(record["id"]), "title": record["title"][:70], "source": record["source"], "url": record["url"],
                                                  "time": attribution.iso(record["ts"]), "window": window, "regulator": record["regulator"], "direction": record["direction"]})
            annos.sort(key=lambda a: a["time"])
            news_company.sort(key=lambda a: a["time"])
            news_industry.sort(key=lambda a: a["time"])
            row = daily_rows.get((symbol, date))
            event = events.get((symbol, date))
            attr = None
            if event:
                attr = {"r": event["r"], "t": event["t"], "shares": event["shares"],
                        "causes": [{**{k: c.get(k) for k in ("f", "p", "w", "lr", "er", "n")},
                                    "label": "成交与盘口交易本身（未被公开信息覆盖的部分）" if c["f"] == "flow" else labels.get(c["f"], c["f"]),
                                    "signals": [{"f": s["f"], "label": labels.get(s["f"], s["f"])} for s in c.get("signals", [])]} for c in event["causes"]],
                        "weak": [labels.get(f, f) for f in event.get("weak", [])],
                        "contrary": [labels.get(f, f) for f in event.get("contrary", [])],
                        "explain": [labels.get(f, f) for f in event.get("explain", [])]}
            days[symbol][str(date)] = {
                "daily_return": attribution.compact(row["return"], 8) if row else None,
                "market_return": attribution.compact(row.get("sample_median_return"), 8) if row else None,
                "down5_breadth": attribution.compact(row.get("down5_breadth"), 4) if row else None,
                "screened": bool(row["screened"]) if row else None,
                "flow": {"volume_ratio": attribution.compact(row.get("volume_ratio"), 3), "spread_ratio": attribution.compact(row.get("spread_ratio"), 3),
                         "imbalance_change": attribution.compact(row.get("imbalance_change"), 4)} if row else None,
                "moneyflow": ({k: (attribution.compact(float(v), 2) if isinstance(v, (int, float)) else None) for k, v in moneyflow[(symbol, date)].items()}
                              if (symbol, date) in moneyflow else None),
                "attribution": attr, "announcements": annos[:30], "news_company": news_company[:20], "news_industry": news_industry[:20],
            }
    pack = {
        "schema_version": 1,
        "metadata": {
            "version": "replay-evidence-v1", "calculation_type": "retrospective_in_sample_association",
            "timezone": "Asia/Shanghai", "attribution_builder_sha256": index["metadata"]["builder_sha256"],
            "evidence_counts": index["metadata"]["evidence_counts"], "windows": attribution.WINDOW_LABELS,
            "min_lr": index["metadata"].get("min_lr"), "thresholds": index["metadata"]["thresholds"],
            "moneyflow_rows": len(moneyflow), "moneyflow": index["metadata"].get("moneyflow"),
            "as_of_rule": "Each item carries its publication time; the replay marks items published at or before the crossing bar's close as known at that moment and later items as disclosed afterwards. The end-of-day attribution uses the full day's windows and is shown as such.",
            "limitations": index["metadata"]["limitations"],
        },
        "symbols": {s: {"name": index["stocks"].get(s, {}).get("name"), "industry": stock_industry.get(s)} for s in symbols},
        "dates": dates, "market_by_date": {str(d): {k: market_by_day.get(d, {}).get(k) for k in ("sample_median_return", "screened_event_rate", "available_at")} for d in dates},
        "macro_by_date": macro_by_date, "days": days,
    }
    result = study.write_json(ROOT / "data" / "replay_evidence.json", pack)
    stock_days = sum(len(v) for v in days.values())
    return {"file": result, "stock_days": stock_days, "attribution_events": sum(1 for s in days.values() for d in s.values() if d["attribution"]),
            "announcements": sum(len(d["announcements"]) for s in days.values() for d in s.values()),
            "company_articles": sum(len(d["news_company"]) for s in days.values() for d in s.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--moneyflow", type=Path)
    args = parser.parse_args()
    print(json.dumps(build(args.daily, args.evidence, args.moneyflow), ensure_ascii=False))


if __name__ == "__main__":
    main()
