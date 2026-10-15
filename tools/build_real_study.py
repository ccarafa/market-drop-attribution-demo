"""Build a reproducible daily descriptive study from existing local aggregates.

Calculations use the standard library. Only reading the private input requires
pyarrow. No network calls, credentials, news claims, or causal probabilities.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, time, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
from statistics import median


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ID = "reports/202607_analysis/daily_features.parquet"
THRESHOLD = -0.05
TOLERANCE = 1e-12
LOCAL_TZ = timezone(timedelta(hours=8))
SOURCE_COLUMNS = [
    "symbol", "date", "last_datetime", "daily_return_proxy",
    "last_price_proxy", "prev_close", "volume_final_shares",
    "spread_bps_mean", "abs_imbalance_mean", "rows",
    "eligible_quote_rows", "zero_top5_volume_rows", "nonpositive_top_quote_rows",
]
COLUMNS = [
    "symbol", "date", "return", "last_price", "prev_close",
    "sample_median_return", "excess_return", "down5_breadth", "baseline_n",
    "volume", "volume_prior5_median", "volume_ratio", "spread_bps",
    "spread_prior5_median", "spread_ratio", "abs_imbalance",
    "imbalance_prior5_median", "imbalance_change", "reference_gap",
    "screened", "flags", "available_at", "last_snapshot_at",
    "raw_sample_median_return", "raw_excess_return", "raw_baseline_n",
    "raw_down5_breadth", "volume_prior5_count", "spread_prior5_count",
    "imbalance_prior5_count",
]
HISTORY_COLUMNS = ["date", "last_price", "return", "volume", "spread_bps", "abs_imbalance", "screened", "flags"]
HISTORY_ROUNDING = {"last_price": 6, "return": 8, "volume": 4, "spread_bps": 6, "abs_imbalance": 8}
FLAG_LABELS = {
    "invalid_return": "收益缺失或无效",
    "invalid_price": "价格或参考价无效",
    "reference_history_missing": "缺少前一观察日价格核验",
    "reference_gap_gt_1pct": "参考价衔接差异超过 1%",
    "extreme_return_gt_50pct": "绝对日收益超过 50%",
    "volume_history_lt_5": "成交量历史不足 5 个有效观察日",
    "spread_history_lt_5": "价差历史不足 5 个有效观察日",
    "imbalance_history_lt_5": "盘口不平衡历史不足 5 个有效观察日",
    "invalid_volume": "成交量缺失或非正",
    "invalid_spread": "价差缺失或为负",
    "invalid_imbalance": "盘口不平衡缺失或超出有效范围",
    "spread_baseline_nonpositive": "历史价差基准不为正",
}
FIELD_LABELS = {
    "symbol": "股票代码", "date": "观察日期", "return": "日收益代理",
    "last_price": "最后有效快照价格", "prev_close": "来源参考前收盘价",
    "sample_median_return": "筛选后样本同期收益中位数（剔除本股）",
    "excess_return": "本股收益减筛选后样本中位数",
    "down5_breadth": "筛选后其他股票跌超 5% 比例", "baseline_n": "筛选后比较股票数",
    "volume": "当日最终累计成交量", "volume_prior5_median": "前 5 个观察日成交量中位数",
    "volume_ratio": "成交量相对历史中位数", "spread_bps": "当日平均价差（基点）",
    "spread_prior5_median": "前 5 个观察日平均价差的中位数", "spread_ratio": "价差相对历史中位数",
    "abs_imbalance": "当日平均绝对盘口不平衡", "imbalance_prior5_median": "前 5 个观察日盘口不平衡中位数",
    "imbalance_change": "盘口不平衡减历史中位数", "reference_gap": "参考前收盘价与前一观察日末价的差异",
    "screened": "符合比较样本筛选条件", "flags": "数据与历史覆盖提示",
    "available_at": "本页日度指标最早展示时间", "last_snapshot_at": "本股最后来源快照时间",
    "raw_sample_median_return": "未按基准差异筛选的样本中位数（剔除本股）",
    "raw_excess_return": "本股收益减未筛选样本中位数",
    "raw_baseline_n": "未筛选比较股票数", "raw_down5_breadth": "未筛选其他股票跌超 5% 比例",
    "volume_prior5_count": "前 5 个观察日有效成交量数",
    "spread_prior5_count": "前 5 个观察日有效价差数",
    "imbalance_prior5_count": "前 5 个观察日有效盘口不平衡数",
}


def number(value):
    """Return a finite Python float or None; booleans are not measurements."""
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def is_drop(value):
    value = number(value)
    return value is not None and value < THRESHOLD - TOLERANCE


def ratio(numerator, denominator):
    a, b = number(numerator), number(denominator)
    return a / b if a is not None and b is not None and b > 0 else None


def history_value(value, kind):
    value = number(value)
    if value is None:
        return None
    if kind == "volume":
        return value if value > 0 else None
    if kind == "spread":
        return value if value >= 0 else None
    return value if 0 <= value <= 1 + TOLERANCE else None


def prior_five(previous, kind):
    # Five previous available rows, not five arbitrarily distant valid values.
    valid = [history_value(r[kind], kind) for r in previous[-5:]]
    valid = [v for v in valid if v is not None]
    return (median(valid) if len(valid) == 5 else None), len(valid)


def normalize_source(records):
    normalized = []
    keys = set()
    for source in records:
        symbol = str(source["symbol"]).zfill(6)
        if not re.fullmatch(r"\d{6}", symbol):
            raise ValueError("Invalid security code")
        date_text = str(source["date"])
        day = datetime.strptime(date_text, "%Y%m%d").date()
        key = (symbol, int(date_text))
        if key in keys:
            raise ValueError("Duplicate security/date source key")
        keys.add(key)
        snapshot = datetime.fromisoformat(str(source["last_datetime"]).strip())
        snapshot = snapshot.replace(tzinfo=LOCAL_TZ) if snapshot.tzinfo is None else snapshot.astimezone(LOCAL_TZ)
        if snapshot.date() != day:
            raise ValueError("Snapshot date differs from observation date")
        release = max(snapshot, datetime.combine(day, time(16), LOCAL_TZ))
        normalized.append({
            "symbol": symbol, "date": int(date_text),
            "return": number(source.get("daily_return_proxy")),
            "last_price": number(source.get("last_price_proxy")),
            "prev_close": number(source.get("prev_close")),
            "volume": number(source.get("volume_final_shares")),
            "spread": number(source.get("spread_bps_mean")),
            "imbalance": number(source.get("abs_imbalance_mean")),
            "last_snapshot_at": snapshot.isoformat(timespec="milliseconds"),
            "_release": release,
        })
    return sorted(normalized, key=lambda r: (r["symbol"], r["date"]))


def leave_one_out(rows, eligible):
    """Exact medians and drop breadth after removal of the target if eligible.

    Each row has a unique _key. Ties are ordered by that key, so removing a tied
    value removes exactly one observation. Sorting costs O(n log n) per date.
    """
    ranked = sorted((r["return"], r["_key"]) for r in rows if eligible(r))
    values = [item[0] for item in ranked]
    positions = {key: i for i, (_, key) in enumerate(ranked)}
    drops = sum(is_drop(v) for v in values)
    output = {}
    for row in rows:
        position = positions.get(row["_key"])
        n = len(values) - (position is not None)
        if n == 0:
            output[row["_key"]] = (None, 0, None)
            continue
        def without(index):
            return values[index + (position is not None and index >= position)]
        middle = n // 2
        center = without(middle) if n % 2 else (without(middle - 1) + without(middle)) / 2
        excluded_drop = int(position is not None and is_drop(row["return"]))
        output[row["_key"]] = (center, n, (drops - excluded_drop) / n)
    return output


def compute_study(records):
    rows = normalize_source(records)
    previous = defaultdict(list)
    by_date = defaultdict(list)
    for row in rows:
        row["_key"] = f"{row['symbol']}_{row['date']}"
        flags = []
        if row["return"] is None:
            flags.append("invalid_return")
        row["_valid_price"] = all(row[k] is not None and row[k] > 0 for k in ("last_price", "prev_close"))
        if not row["_valid_price"]:
            flags.append("invalid_price")
        prior = previous[row["symbol"]]
        gap_ratio = ratio(row["prev_close"], prior[-1]["last_price"]) if prior else None
        row["reference_gap"] = gap_ratio - 1 if gap_ratio is not None and row["prev_close"] > 0 else None
        if row["reference_gap"] is None:
            flags.append("reference_history_missing")
        elif abs(row["reference_gap"]) > 0.01 + TOLERANCE:
            flags.append("reference_gap_gt_1pct")
        if row["return"] is not None and abs(row["return"]) > 0.5 + TOLERANCE:
            flags.append("extreme_return_gt_50pct")
        for kind in ("volume", "spread", "imbalance"):
            center, count = prior_five(prior, kind)
            row[f"{kind}_prior5_median"] = center
            row[f"{kind}_prior5_count"] = count
            if count < 5:
                flags.append(f"{kind}_history_lt_5")
            current = history_value(row[kind], kind)
            if current is None:
                flags.append(f"invalid_{kind}")
            if kind == "imbalance":
                row["imbalance_change"] = current - center if current is not None and center is not None else None
            else:
                row[f"{kind}_ratio"] = ratio(current, center)
            if kind == "spread" and center is not None and center <= 0:
                flags.append("spread_baseline_nonpositive")
        row["flags"] = flags
        row["_raw_eligible"] = row["return"] is not None and row["_valid_price"]
        row["screened"] = row["_raw_eligible"] and not any(f in flags for f in ("reference_gap_gt_1pct", "extreme_return_gt_50pct"))
        row["spread_bps"] = row["spread"]
        row["abs_imbalance"] = row["imbalance"]
        prior.append(row)
        by_date[row["date"]].append(row)

    market = []
    for date, members in sorted(by_date.items()):
        # Same-day peer aggregates require every included daily record. Release
        # the complete daily view only after both 16:00 and the latest snapshot.
        release = max(r["_release"] for r in members).isoformat(timespec="seconds")
        raw = leave_one_out(members, lambda r: r["_raw_eligible"])
        screened = leave_one_out(members, lambda r: r["screened"])
        for row in members:
            center, n, breadth = screened[row["_key"]]
            raw_center, raw_n, raw_breadth = raw[row["_key"]]
            row.update({
                "sample_median_return": center, "baseline_n": n, "down5_breadth": breadth,
                "raw_sample_median_return": raw_center, "raw_baseline_n": raw_n,
                "raw_down5_breadth": raw_breadth, "available_at": release,
                "excess_return": row["return"] - center if row["return"] is not None and center is not None else None,
                "raw_excess_return": row["return"] - raw_center if row["return"] is not None and raw_center is not None else None,
            })
        raw_pool = [r["return"] for r in members if r["_raw_eligible"]]
        screened_pool = [r["return"] for r in members if r["screened"]]
        raw_events = sum(is_drop(r["return"]) for r in members)
        screened_events = sum(is_drop(r["return"]) and r["screened"] for r in members)
        market.append({
            "date": date, "stock_days": len(members), "valid_returns": len(raw_pool),
            "raw_baseline_n": len(raw_pool), "baseline_n": len(screened_pool),
            "raw_events": raw_events, "screened_events": screened_events,
            "raw_event_rate": ratio(sum(is_drop(v) for v in raw_pool), len(raw_pool)),
            "screened_event_rate": ratio(screened_events, len(screened_pool)),
            "raw_sample_median_return": median(raw_pool) if raw_pool else None,
            "sample_median_return": median(screened_pool) if screened_pool else None,
            "reference_gap_gt_1pct": sum("reference_gap_gt_1pct" in r["flags"] for r in members),
            "extreme_return_gt_50pct": sum("extreme_return_gt_50pct" in r["flags"] for r in members),
            "reference_history_missing": sum("reference_history_missing" in r["flags"] for r in members),
            "available_at": release,
        })
    events = sorted((r for r in rows if is_drop(r["return"])), key=lambda r: (r["date"], r["symbol"]))
    all_flags = Counter(flag for row in rows for flag in row["flags"])
    event_flags = Counter(flag for row in events for flag in row["flags"])
    summary = {
        "stocks": len(previous), "dates": len(by_date), "stock_days": len(rows),
        "start_date": min(by_date) if by_date else None, "end_date": max(by_date) if by_date else None,
        "raw_events": len(events), "screened_events": sum(r["screened"] for r in events),
        "event_stocks": len({r["symbol"] for r in events}),
        "valid_return_rows": sum(r["return"] is not None for r in rows),
        "raw_baseline_rows": sum(r["_raw_eligible"] for r in rows),
        "screened_stock_days": sum(r["screened"] for r in rows),
        "excluded_stock_days": sum(not r["screened"] for r in rows),
        "flag_counts": dict(sorted(all_flags.items())),
        "event_flag_counts": dict(sorted(event_flags.items())),
        "events_with_all_prior5": sum(all(r[f"{k}_prior5_count"] == 5 for k in ("volume", "spread", "imbalance")) for r in events),
    }
    return rows, events, market, summary


def array(row):
    return [row[key] for key in COLUMNS]


def history_array(row):
    """Display-only projection; full precision events and computations retained."""
    return [round(row[key], HISTORY_ROUNDING[key]) if key in HISTORY_ROUNDING and row[key] is not None else row[key]
            for key in HISTORY_COLUMNS]


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(payload)
    return {"file": path.relative_to(ROOT).as_posix(), "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}


def build(source):
    # Import only for the local source reader; unit tests need no private input
    # and no third-party packages.
    import pyarrow.parquet as pq
    records = pq.read_table(source, columns=SOURCE_COLUMNS).to_pylist()
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    code_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    rows, events, market, summary = compute_study(records)
    expected = (119652, 5210, 23, 13246)
    actual = (summary["stock_days"], summary["stocks"], summary["dates"], summary["raw_events"])
    if actual != expected:
        raise ValueError("Source coverage or strict event count differs from verified study input")
    if (summary["start_date"], summary["end_date"]) != (20260701, 20260731):
        raise ValueError("Source period differs from verified study input")
    reconstruction = []
    for row in rows:
        price_return = ratio(row["last_price"], row["prev_close"])
        if price_return is not None and row["return"] is not None:
            reconstruction.append(abs(price_return - 1 - row["return"]))
    if reconstruction and max(reconstruction) > 1e-10:
        raise ValueError("Source return does not reconstruct from source prices")
    complete = [r for r in events if r["screened"] and r["reference_gap"] is not None and
                all(r[f"{k}_prior5_count"] == 5 for k in ("volume", "spread", "imbalance"))]
    default = max(complete, key=lambda r: (r["date"], -r["return"], r["symbol"])) if complete else events[0]
    source_diagnostics = {
        "snapshot_rows": sum(int(r["rows"]) for r in records),
        "eligible_quote_rows": sum(int(r["eligible_quote_rows"]) for r in records),
        "zero_top5_volume_rows": sum(int(r["zero_top5_volume_rows"]) for r in records),
        "nonpositive_top_quote_rows": sum(int(r["nonpositive_top_quote_rows"]) for r in records),
        "source_return_reconstruction_max_absolute_error": max(reconstruction, default=0),
        "reference_pairs_checked": sum(r["reference_gap"] is not None for r in rows),
        "reference_pairs_not_close_rtol_1e8": sum(r["reference_gap"] is not None and abs(r["reference_gap"]) > 1e-8 for r in rows),
    }
    metadata = {
        "version": "daily-descriptive-v1", "scope": "existing_local_real_daily_aggregates",
        "calculation_type": "retrospective_descriptive", "source_id": SOURCE_ID,
        "source_sha256": source_hash, "builder_sha256": code_hash,
        "timezone": "Asia/Shanghai", "timezone_basis": "display convention; source dictionary did not independently confirm timezone",
        "default_event_id": default["_key"], "event_id_format": "symbol_date",
        "event_threshold": THRESHOLD, "boundary_tolerance": TOLERANCE,
        "price_basis": "unadjusted last valid snapshot and source reference previous close; not guaranteed official close",
        "availability": "Each date is released at the later of 16:00 Asia/Shanghai and the latest source snapshot for that date; daily retrospective only.",
        "benchmark": "Same-date sample median excluding the target security; not an official index or causal contribution.",
        "screening": "Keep finite returns and positive source prices; exclude known absolute reference gaps >1% and absolute returns >50%, with 1e-12 boundary tolerance. Unknown previous-observation reference checks are retained and flagged. Raw events remain visible.",
        "prior_history": "Use the five immediately preceding available stock-day observations, excluding current day. Require five valid values; missing history gives null, never zero or future filling.",
        "return_unit": "decimal_fraction", "spread_unit": "basis_points", "volume_unit": "source shares convention",
        "flag_labels": FLAG_LABELS, "field_labels": FIELD_LABELS,
        "source_diagnostics": source_diagnostics,
        "limitations": [
            "仅覆盖 2026 年 7 月 23 个观察交易日，非完整多年历史或实时行情。",
            "未复权价格及参考前收盘口径仍可能包含公司行动、上市或供应商定义差异。",
            "同期差值、成交量和流动性变化是观测证据，不是原因、贡献概率或因果结论。",
            "没有新增行业、新闻、公告或财报证据；不拟合长期 beta。",
            "全日指标只在当日信息完整后展示；不用于声称当时盘中已知或可预测。",
            "筛选后样本仍保留无法核验前一日价格的记录，并以提示标识；筛选不代表质量认证。",
        ],
        "method_file": "docs/REAL_DATA_METHOD.md", "demo_path": "demo.html",
        "history_display_rounding": HISTORY_ROUNDING,
        "history_projection": "Compact history is for charts; security code is the symbols dictionary key and availability comes from market_by_date. All event evidence and calculations retain full precision.",
        "scope_authorization": "User authorized using existing local real stock data for this new study; retained fictional demonstration remains separately labeled Demo.",
    }
    shards = defaultdict(lambda: defaultdict(list))
    for row in rows:
        shards[row["symbol"][0]][row["symbol"]].append(history_array(row))
    artifacts = []
    shard_manifest = {}
    for prefix, symbols in sorted(shards.items()):
        result = write_json(ROOT / "data" / "series" / f"{prefix}.json", {
            "schema_version": 1, "prefix": prefix, "columns": HISTORY_COLUMNS,
            "display_rounding": HISTORY_ROUNDING, "symbols": dict(sorted(symbols.items())),
        })
        shard_manifest[prefix] = {**result, "stocks": len(symbols), "stock_days": sum(len(v) for v in symbols.values())}
        artifacts.append(result)
    study = {
        "schema_version": 1, "metadata": metadata, "summary": summary,
        "dates": sorted({r["date"] for r in rows}), "market_by_date": market,
        "columns": COLUMNS, "events": [array(r) for r in events],
        "series": {"path_template": "data/series/{prefix}.json", "prefix_length": 1, "shards": shard_manifest},
    }
    artifacts.append(write_json(ROOT / "data" / "study.json", study))
    examples = {"default_event_id": default["_key"]}
    for flag in ("reference_gap_gt_1pct", "reference_history_missing", "volume_history_lt_5"):
        candidates = [r for r in events if flag in r["flags"]]
        examples[flag] = candidates[0]["_key"] if candidates else None
    audit = {
        "schema_version": 1, "source_id": SOURCE_ID, "source_sha256": source_hash,
        "builder_sha256": code_hash, "summary": summary, "source_diagnostics": source_diagnostics,
        "checks": {"source_coverage": "passed", "strict_event_count": "passed", "source_return_reconstruction": "passed", "unique_stock_date_keys": "passed", "json_finite_numbers": "passed"},
        "examples": examples, "artifacts": artifacts,
    }
    write_json(ROOT / ".runtime" / "study-audit.json", audit)
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, help="Local daily aggregate Parquet; not published")
    args = parser.parse_args()
    source = args.source
    if source is None:
        source = next((ancestor / SOURCE_ID for ancestor in ROOT.parents if (ancestor / SOURCE_ID).is_file()), None)
    if source is None or not source.is_file():
        parser.error("Source is unavailable; provide --source with the local daily aggregate Parquet")
    result = build(source)
    print(json.dumps({"summary": result["summary"], "examples": result["examples"], "source_diagnostics": result["source_diagnostics"], "artifacts": result["artifacts"]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
