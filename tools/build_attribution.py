"""Build the attribution study: daily events, evidence matching and calibrated
cause probabilities from local daily aggregates and provider evidence metadata.

Inputs (all private, never published):
  --daily     local daily aggregate Parquet (same source as build_real_study)
  --evidence  directory with announcements/YYYYMMDD.jsonl and news/YYYYMMDD.jsonl
  --intraday  optional stock-day order-flow summary Parquet from the snapshot job

Outputs under data/attribution/: index.json (metadata, calibration tables,
stock dictionary) and events/YYYYMMDD.json shards holding each event's causes
and the titles, times and links of the evidence behind them. Full texts, raw
provider records and credentials never enter the output.

Method in one paragraph: a stock-day return r is split into the same-date
sample median m (market), the same-industry median minus m (industry) and the
remainder (stock-specific). Evidence features are booleans per stock-day
(company announcement families by publication window, company/industry news,
peer announcements, trading-flow anomalies). For each direction and threshold
the sample gives P(feature | event) and P(feature | no event); the difference
(excess prevalence) weights the features present for an event, and the
stock-specific share is distributed in proportion. Trading-flow evidence is
always a candidate, so an event without public information still receives a
stated reason. All numbers are in-sample associations, not causal proof.
"""
from __future__ import annotations

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter, defaultdict
from datetime import date as date_type, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_real_study as study  # noqa: E402
from attribution_taxonomy import FAMILIES, INDUSTRY_KEYWORDS, classify_announcement, classify_news  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BEIJING = timezone(timedelta(hours=8))
THRESHOLDS = (0.02, 0.03, 0.05)
TOLERANCE = 1e-12
MIN_INDUSTRY_PEERS = 8
PEER_SHARE = 0.10  # peer filings count only when at least this share of the industry files
SMOOTHING = 0.5
MIN_SUPPORT = 30   # feature observations below this shrink the weight linearly
MIN_LR = 1.5       # weaker associations are listed as evidence but not credited as causes
FLOW_FLOOR = 0.02
CALENDAR_BEFORE = [20260622, 20260623, 20260624, 20260625, 20260626, 20260629, 20260630]
CALENDAR_AFTER = [20260803]
WINDOW_LABELS = {
    "pre_open": "前一交易日收盘后至当日开盘前",
    "intraday": "当日交易时段",
    "post_close": "当日收盘后（事后披露）",
    "prior": "此前 2 至 5 个交易日",
}
FLOW_LABELS = {
    "volume_ratio_ge2": "成交量达到前 5 日中位数 2 倍以上",
    "volume_ratio_ge4": "成交量达到前 5 日中位数 4 倍以上",
    "spread_ratio_ge1_5": "平均价差达到前 5 日中位数 1.5 倍以上",
    "imbalance_up": "盘口绝对不平衡较前 5 日中位数上升 0.1 以上",
    "gap_open": "开盘跳空同向超过 2%",
    "net_sell_pressure": "主动卖出量占比领先 15 个百分点以上（快照 tick 规则）",
    "net_buy_pressure": "主动买入量占比领先 15 个百分点以上（快照 tick 规则）",
    "big_prints": "单笔 20 万元以上成交占成交额四成以上",
    "limit_down_touched": "触及跌停价",
    "limit_up_touched": "触及涨停价",
    "main_outflow_heavy": "主力（大单与超大单）净流出达到当日成交额一成以上",
    "main_inflow_heavy": "主力（大单与超大单）净流入达到当日成交额一成以上",
    "xl_outflow_heavy": "超大单净流出达到当日成交额半成以上",
    "xl_inflow_heavy": "超大单净流入达到当日成交额半成以上",
    "retail_absorbing": "散户（小单与中单）净流入同时主力净流出，接盘特征",
}
MONEYFLOW_FIELDS = ("turnoverValue", "turnoverVol", "dealAmount", "netFlow", "inflow", "outflow", "mainFlow", "smainFlow",
                    "netFlowS", "netFlowM", "netFlowL", "netFlowXL", "mainInflow", "mainOutflow")


def load_moneyflow(directory):
    """(symbol, date) -> provider daily money-flow record (main/large/extra-large order splits)."""
    out = {}
    if not directory:
        return out
    for path in sorted(Path(directory).glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not payload.get("complete"):
            continue
        for row in payload.get("rows", []):
            code = str(row.get("ticker") or "").zfill(6)
            day = str(row.get("tradeDate") or "").replace("-", "")
            if len(code) == 6 and code.isdigit() and day.isdigit():
                out[(code, int(day))] = {k: row.get(k) for k in MONEYFLOW_FIELDS}
    return out


def moneyflow_features(mf):
    feats = set()
    if not mf:
        return feats
    turnover = mf.get("turnoverValue")
    if not isinstance(turnover, (int, float)) or turnover <= 0:
        return feats
    main = mf.get("mainFlow") or 0.0
    xl = mf.get("netFlowXL") or 0.0
    small = mf.get("smainFlow") or 0.0
    if main / turnover <= -0.10:
        feats.add("main_outflow_heavy")
    if main / turnover >= 0.10:
        feats.add("main_inflow_heavy")
    if xl / turnover <= -0.05:
        feats.add("xl_outflow_heavy")
    if xl / turnover >= 0.05:
        feats.add("xl_inflow_heavy")
    if main < 0 and small > 0 and small / turnover >= 0.05:
        feats.add("retail_absorbing")
    return feats
INFORMATIVE = {k for k, v in FAMILIES.items() if v[2]}
NEWS_LABELS = {
    "news_company": "直接提及本股的财经文章",
    "news_industry_regulator": "涉及本行业的监管或政策报道",
    "news_industry": "本行业相关报道增多（2 篇以上）",
    "peer_announcements": "同行业公司集中披露同向重要公告（不少于行业样本的一成且至少 3 家）",
}


def ms_at(day, hour, minute):
    d = datetime.strptime(str(day), "%Y%m%d").replace(tzinfo=BEIJING)
    return int(d.replace(hour=hour, minute=minute).timestamp() * 1000)


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, BEIJING).isoformat(timespec="minutes")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_jsonl(directory):
    items = []
    for path in sorted(Path(directory).glob("*.jsonl")):
        with path.open(encoding="utf-8") as handle:
            items.extend(json.loads(line) for line in handle if line.strip())
    return items


class Calendar:
    def __init__(self, sample_dates):
        self.days = sorted(set(CALENDAR_BEFORE) | set(sample_dates) | set(CALENDAR_AFTER))
        self.index = {d: i for i, d in enumerate(self.days)}

    def shift(self, day, offset):
        i = self.index[day] + offset
        return self.days[i] if 0 <= i < len(self.days) else None

    def windows(self, day):
        prev, nxt, p5 = self.shift(day, -1), self.shift(day, 1), self.shift(day, -5)
        if prev is None or nxt is None or p5 is None:
            return None
        return {
            "pre_open": (ms_at(prev, 15, 0), ms_at(day, 9, 30)),
            "intraday": (ms_at(day, 9, 30), ms_at(day, 15, 0)),
            "post_close": (ms_at(day, 15, 0), ms_at(nxt, 9, 30)),
            "prior": (ms_at(p5, 15, 0), ms_at(prev, 15, 0)),
        }


class Index:
    """Timestamp-sorted evidence per key with half-open window slicing."""

    def __init__(self):
        self.store = defaultdict(list)
        self.sorted = False

    def add(self, key, ts, item):
        self.store[key].append((ts, item))

    def finish(self):
        for key in self.store:
            self.store[key].sort(key=lambda pair: pair[0])
        self.times = {key: [t for t, _ in pairs] for key, pairs in self.store.items()}
        self.sorted = True

    def between(self, key, start, end):
        if key not in self.store:
            return []
        times = self.times[key]
        lo, hi = bisect_right(times, start), bisect_right(times, end)
        return [item for _, item in self.store[key][lo:hi]]


def build_evidence(evidence_dir):
    announcements = load_jsonl(Path(evidence_dir) / "announcements")
    news = load_jsonl(Path(evidence_dir) / "news")
    names, industries = defaultdict(Counter), defaultdict(Counter)
    anno_index = Index()
    for item in announcements:
        code = str(item.get("stockId") or "").zfill(6)
        if not code.isdigit() or len(code) != 6 or not item.get("scannedTimestamp"):
            continue
        family, direction, informational = classify_announcement(item.get("zsAutoCategory"), item.get("autoCategory"), item.get("title"))
        record = {"id": item.get("id"), "ts": int(item["scannedTimestamp"]), "title": str(item.get("title") or "")[:90],
                  "family": family, "direction": direction, "informational": informational,
                  "category": item.get("zsAutoCategory") or item.get("autoCategory"), "important": int(item.get("isImportant") or 0)}
        anno_index.add(code, record["ts"], record)
        if item.get("stockName"):
            names[code][item["stockName"]] += 1
        if item.get("industry"):
            industries[code][item["industry"]] += 1
    anno_index.finish()
    stock_industry = {code: counter.most_common(1)[0][0] for code, counter in industries.items()}
    stock_name = {code: counter.most_common(1)[0][0] for code, counter in names.items()}
    news_company, news_industry, news_macro = Index(), Index(), Index()
    for item in news:
        if not item.get("publishTime"):
            continue
        tags = classify_news(item.get("title"), item.get("tickers"), stock_industry)
        record = {"id": item.get("newsId"), "ts": int(item["publishTime"]), "title": str(item.get("title") or "")[:90],
                  "source": item.get("source"), "url": item.get("url"), "regulator": tags["regulator"],
                  "macro": tags["macro"], "direction": tags["direction"], "industries": tags["industries"]}
        for code in tags["tickers"]:
            news_company.add(code, record["ts"], record)
        for industry in tags["industries"]:
            news_industry.add(industry, record["ts"], record)
        if tags["macro"] or tags["market_wide"] or (tags["regulator"] and not tags["industries"]):
            news_macro.add("all", record["ts"], record)
    for index in (news_company, news_industry, news_macro):
        index.finish()
    return {"anno": anno_index, "news_company": news_company, "news_industry": news_industry, "news_macro": news_macro,
            "stock_industry": stock_industry, "stock_name": stock_name,
            "counts": {"announcements": len(announcements), "news": len(news), "stocks_with_industry": len(stock_industry)}}


def industry_medians(by_date, stock_industry):
    """Leave-one-out same-industry median of screened returns per stock-day."""
    out = {}
    for day, members in by_date.items():
        groups = defaultdict(list)
        for row in members:
            industry = stock_industry.get(row["symbol"])
            if row["screened"] and industry:
                groups[industry].append(row)
        for industry, rows in groups.items():
            rows.sort(key=lambda r: (r["return"], r["_key"]))
            values = [r["return"] for r in rows]
            n = len(values)
            for i, row in enumerate(rows):
                m = n - 1
                if m < MIN_INDUSTRY_PEERS:
                    continue
                def without(index):
                    return values[index + (index >= i)]
                middle = m // 2
                center = without(middle) if m % 2 else (without(middle - 1) + without(middle)) / 2
                out[row["_key"]] = (center, m)
        for row in members:
            industry = stock_industry.get(row["symbol"])
            if industry and row["_key"] not in out:
                peers = [r for r in groups.get(industry, []) if r["_key"] != row["_key"]]
                if len(peers) >= MIN_INDUSTRY_PEERS:
                    values = sorted(r["return"] for r in peers)
                    middle = len(values) // 2
                    center = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
                    out[row["_key"]] = (center, len(peers))
    return out


def flow_features(row, intraday):
    feats = set()
    vr = row.get("volume_ratio")
    if vr is not None and vr >= 2:
        feats.add("volume_ratio_ge2")
    if vr is not None and vr >= 4:
        feats.add("volume_ratio_ge4")
    sr = row.get("spread_ratio")
    if sr is not None and sr >= 1.5:
        feats.add("spread_ratio_ge1_5")
    ic = row.get("imbalance_change")
    if ic is not None and ic >= 0.1:
        feats.add("imbalance_up")
    if intraday:
        r = row["return"]
        gap = intraday.get("gap_return")
        if gap is not None and r is not None and abs(gap) >= 0.02 and gap * r > 0:
            feats.add("gap_open")
        nb = intraday.get("net_buy_share")
        if nb is not None and nb <= -0.15:
            feats.add("net_sell_pressure")
        if nb is not None and nb >= 0.15:
            feats.add("net_buy_pressure")
        bp = intraday.get("big_print_share")
        if bp is not None and bp >= 0.4:
            feats.add("big_prints")
        if (intraday.get("at_low_limit_snaps") or 0) > 0:
            feats.add("limit_down_touched")
        if (intraday.get("at_high_limit_snaps") or 0) > 0:
            feats.add("limit_up_touched")
    return feats


def direction_tag(direction):
    return "neg" if direction < 0 else ("pos" if direction > 0 else "neu")


def stock_day_features(row, windows, evidence, industry, peers_by_day, industry_size):
    """Boolean feature set and the evidence items for one stock-day."""
    feats, items = set(), {"announcements": [], "news_company": [], "news_industry": []}
    code = row["symbol"]
    for window, (start, end) in windows.items():
        for record in evidence["anno"].between(code, start, end):
            if record["informational"] or window in ("pre_open", "intraday"):
                items["announcements"].append({"id": record["id"], "title": record["title"][:70], "family": record["family"],
                                               "direction": record["direction"], "time": iso(record["ts"]), "window": window})
            if record["informational"] and window != "post_close":
                feats.add(f"anno:{record['family']}:{direction_tag(record['direction'])}:{window}")
            if window == "post_close":
                feats.add(f"explain:{record['family']}")
        if window == "post_close":
            continue
        for record in evidence["news_company"].between(code, start, end):
            if len(items["news_company"]) < 8:
                items["news_company"].append({"id": record["id"], "title": record["title"], "source": record["source"],
                                              "url": record["url"], "time": iso(record["ts"]), "window": window, "direction": record["direction"]})
            feats.add(f"news_company:{window}")
        if industry and window in ("pre_open", "intraday"):
            hits = evidence["news_industry"].between(industry, start, end)
            regulator = [h for h in hits if h["regulator"]]
            for tag, sign in (("neg", -1), ("pos", 1)):
                if any(h["direction"] == sign for h in regulator):
                    feats.add(f"news_industry_regulator:{tag}:{window}")
            if regulator:
                feats.add(f"news_industry_regulator:neu:{window}")
            if len(hits) >= 2:
                feats.add(f"news_industry:{window}")
            for record in (regulator or hits)[:4]:
                items["news_industry"].append({"id": record["id"], "title": record["title"], "source": record["source"],
                                               "url": record["url"], "time": iso(record["ts"]), "window": window,
                                               "regulator": record["regulator"]})
    if industry:
        needed = max(3, math.ceil(PEER_SHARE * industry_size.get(industry, 0)))
        for window in ("pre_open", "prior"):
            counts = peers_by_day.get((industry, row["date"], window), {})
            for tag in ("neg", "pos"):
                if len(counts.get(tag, set()) - {code}) >= needed:
                    feats.add(f"peer_announcements:{tag}:{window}")
    items["announcements"] = sorted(items["announcements"], key=lambda a: (a["family"] not in INFORMATIVE, a["time"]))[:8]
    items["news_industry"] = items["news_industry"][:4]
    return feats, items


def peer_counts(rows, calendar, evidence, stock_industry):
    """Per (industry, date, window): stocks with informational pos/neg announcements."""
    counts = defaultdict(lambda: defaultdict(set))
    cache = {}
    for row in rows:
        industry = stock_industry.get(row["symbol"])
        if not industry:
            continue
        windows = cache.setdefault(row["date"], calendar.windows(row["date"]))
        if not windows:
            continue
        for window in ("pre_open", "prior"):
            start, end = windows[window]
            for record in evidence["anno"].between(row["symbol"], start, end):
                if record["informational"] and record["direction"]:
                    counts[(industry, row["date"], window)][direction_tag(record["direction"])].add(row["symbol"])
    return counts


def feature_label(feature):
    kind, *parts = feature.split(":")
    if kind == "anno":
        family, tag, window = parts
        if family == "abnormal_trading":
            return f"此前已发布交易异常波动公告，波动延续（{WINDOW_LABELS[window]}）"
        return f"本公司公告：{FAMILIES[family][0]}（{WINDOW_LABELS[window]}）"
    if kind == "explain":
        return f"事后披露：{FAMILIES[parts[0]][0]}"
    if kind == "news_company":
        return f"{NEWS_LABELS['news_company']}（{WINDOW_LABELS[parts[0]]}）"
    if kind == "news_industry_regulator":
        tag, window = parts
        tone = {"neg": "，标题偏负面", "pos": "，标题偏正面", "neu": ""}[tag]
        return f"{NEWS_LABELS[kind]}{tone}（{WINDOW_LABELS[window]}）"
    if kind == "news_industry":
        return f"{NEWS_LABELS[kind]}（{WINDOW_LABELS[parts[0]]}）"
    if kind == "peer_announcements":
        return f"{NEWS_LABELS[kind]}（{WINDOW_LABELS[parts[1]]}）"
    return FLOW_LABELS.get(feature, feature)


def feature_consistent(feature, direction):
    """Whether a feature can explain a move in the given direction (-1 down, +1 up)."""
    kind, *parts = feature.split(":")
    if kind in ("anno", "peer_announcements", "news_industry_regulator"):
        tag = parts[1] if kind == "anno" else parts[0]
        return tag == "neu" or (tag == "neg") == (direction < 0)
    if feature in ("net_sell_pressure", "limit_down_touched", "main_outflow_heavy", "xl_outflow_heavy", "retail_absorbing"):
        return direction < 0
    if feature in ("net_buy_pressure", "limit_up_touched", "main_inflow_heavy", "xl_inflow_heavy"):
        return direction > 0
    return kind != "explain"


def is_flow(feature):
    return feature in FLOW_LABELS


def calibrate(population, direction, threshold):
    """Excess prevalence and likelihood ratio of each feature for events of one direction/threshold."""
    event_counter, other_counter = Counter(), Counter()
    n_event = n_other = 0
    for row in population:
        r = row["return"]
        is_event = (r < -threshold - TOLERANCE) if direction < 0 else (r > threshold + TOLERANCE)
        if is_event:
            n_event += 1
            event_counter.update(row["_features"])
        else:
            n_other += 1
            other_counter.update(row["_features"])
    table = {}
    for feature in set(event_counter) | set(other_counter):
        p_event = (event_counter[feature] + SMOOTHING) / (n_event + 2 * SMOOTHING)
        p_other = (other_counter[feature] + SMOOTHING) / (n_other + 2 * SMOOTHING)
        table[feature] = {"events_with": event_counter[feature], "others_with": other_counter[feature],
                          "p_given_event": p_event, "p_given_other": p_other,
                          "likelihood_ratio": p_event / p_other, "excess": p_event - p_other,
                          "event_rate_given_feature": (event_counter[feature] + SMOOTHING) / (event_counter[feature] + other_counter[feature] + 2 * SMOOTHING)}
    return {"direction": direction, "threshold": threshold, "events": n_event, "others": n_other,
            "base_rate": n_event / (n_event + n_other) if n_event + n_other else None, "features": table}


def clip01(x):
    return max(0.0, min(1.0, x))


def score_event(row, calibration):
    r = row["return"]
    direction = -1 if r < 0 else 1
    m = row.get("sample_median_return")
    industry_center = row.get("_industry_median")
    market_share = clip01(m / r) if m is not None and m * r > 0 else 0.0
    industry_share = 0.0
    if industry_center is not None and m is not None and (industry_center - m) * r > 0:
        industry_share = clip01((industry_center - m) / r)
    common = market_share + industry_share
    if common > 1:
        market_share, industry_share, common = market_share / common, industry_share / common, 1.0
    idio = 1.0 - common
    table = calibration["features"]
    candidates, contrary, explanations = [], [], []
    for feature in sorted(row["_features"]):
        kind = feature.split(":")[0]
        if kind == "explain":
            explanations.append({"f": feature, **_stats(table.get(feature))})
            continue
        entry = {"f": feature, **_stats(table.get(feature))}
        if feature_consistent(feature, direction):
            candidates.append(entry)
        else:
            contrary.append(entry)
    flow_items = [c for c in candidates if is_flow(c["f"])]
    info_items = [c for c in candidates if not is_flow(c["f"]) and c["w"] > 0]
    weak = [c for c in candidates if not is_flow(c["f"]) and c["w"] <= 0]
    flow_weight = sum(c["w"] for c in flow_items) + FLOW_FLOOR
    total = flow_weight + sum(c["w"] for c in info_items)
    causes = [{**c, "p": idio * c["w"] / total} for c in info_items]
    causes.append({"f": "flow", "signals": flow_items, "w": flow_weight, "p": idio * flow_weight / total})
    causes.sort(key=lambda c: -c["p"])
    return {"direction": direction, "market_share": market_share, "industry_share": industry_share,
            "stock_specific_share": idio, "causes": causes, "contrary": contrary, "explanations": explanations, "weak": weak}


def _stats(entry):
    """Weight = attributable fraction among the exposed, 1 - 1/LR, shrunk at low support.

    Associations below MIN_LR are too weak to credit as causes and get weight 0.
    """
    if not entry:
        return {"w": 0.0, "lr": None, "er": None, "n": 0}
    support = entry["events_with"] + entry["others_with"]
    lr = entry["likelihood_ratio"]
    weight = (1.0 - 1.0 / lr) * min(1.0, support / MIN_SUPPORT) if lr >= MIN_LR else 0.0
    return {"w": weight, "lr": lr, "er": entry["event_rate_given_feature"], "n": entry["events_with"]}


def threshold_level(r):
    a = abs(r)
    for t in sorted(THRESHOLDS, reverse=True):
        if a > t + TOLERANCE:
            return t
    return None


def compact(number, digits=6):
    return None if number is None or not math.isfinite(number) else round(number, digits)


def build(daily, evidence_dir, intraday_path, out_dir, moneyflow_dir=None):
    import pyarrow.parquet as pq
    records = pq.read_table(daily, columns=study.SOURCE_COLUMNS).to_pylist()
    rows, strict_events, market, summary = study.compute_study(records)
    evidence = build_evidence(evidence_dir)
    moneyflow = load_moneyflow(moneyflow_dir)
    stock_industry = evidence["stock_industry"]
    calendar = Calendar(sorted({r["date"] for r in rows}))
    by_date = defaultdict(list)
    for row in rows:
        by_date[row["date"]].append(row)
    ind_med = industry_medians(by_date, stock_industry)
    intraday = {}
    if intraday_path:
        for item in pq.read_table(intraday_path).to_pylist():
            intraday[(str(item["symbol"]).zfill(6), int(item["date"]))] = item
    peers = peer_counts(rows, calendar, evidence, stock_industry)
    industry_size = Counter(stock_industry.get(code) for code in {r["symbol"] for r in rows} if stock_industry.get(code))
    windows_cache = {}
    population = []
    for row in rows:
        row["_industry_median"], row["_industry_peers"] = ind_med.get(row["_key"], (None, 0))
        windows = windows_cache.setdefault(row["date"], calendar.windows(row["date"]))
        feats, items = stock_day_features(row, windows, evidence, stock_industry.get(row["symbol"]), peers, industry_size)
        feats |= flow_features(row, intraday.get((row["symbol"], row["date"])))
        row["_mf"] = moneyflow.get((row["symbol"], row["date"]))
        feats |= moneyflow_features(row["_mf"])
        row["_features"], row["_items"] = feats, items
        if row["screened"] and row["return"] is not None:
            population.append(row)
    calibrations = {}
    for direction in (-1, 1):
        for threshold in THRESHOLDS:
            calibrations[f"{'down' if direction < 0 else 'up'}:{threshold}"] = calibrate(population, direction, threshold)
    event_keys = {r["_key"] for r in rows if r["return"] is not None and threshold_level(r["return"]) is not None}
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "events").mkdir(exist_ok=True)
    shard_manifest, counts = {}, Counter()
    market_by_day = {m["date"]: m for m in market}
    for day, members in sorted(by_date.items()):
        day_events = []
        for row in sorted((r for r in members if r["_key"] in event_keys), key=lambda r: r["return"]):
            level = threshold_level(row["return"])
            key = f"{'down' if row['return'] < 0 else 'up'}:{level}"
            scored = score_event(row, calibrations[key])
            counts[key] += 1
            flow = intraday.get((row["symbol"], row["date"])) or {}
            event = {
                "id": row["_key"], "symbol": row["symbol"], "date": row["date"], "r": compact(row["return"], 8),
                "price": compact(row["last_price"]), "prev_close": compact(row["prev_close"]), "t": level,
                "screened": row["screened"], "m": compact(row.get("sample_median_return"), 8),
                "i": compact(row["_industry_median"], 8), "peers": row["_industry_peers"],
                "breadth": compact(row.get("down5_breadth"), 4), "vr": compact(row.get("volume_ratio"), 3),
                "sr": compact(row.get("spread_ratio"), 3), "ic": compact(row.get("imbalance_change"), 4),
                "shares": [compact(scored["market_share"], 4), compact(scored["industry_share"], 4), compact(scored["stock_specific_share"], 4)],
                "causes": [_round_cause(c) for c in scored["causes"]],
                "contrary": [c["f"] for c in scored["contrary"]],
                "weak": [c["f"] for c in scored["weak"]],
                "explain": [c["f"] for c in scored["explanations"]],
                "ev": row["_items"],
            }
            if row["flags"]:
                event["flags"] = row["flags"]
            if row.get("_mf"):
                mf = row["_mf"]
                event["mf"] = {k: (compact(float(v), 2) if isinstance(v, (int, float)) else None) for k, v in mf.items()
                               if k in ("turnoverValue", "dealAmount", "netFlow", "mainFlow", "smainFlow", "netFlowS", "netFlowM", "netFlowL", "netFlowXL")}
            if flow:
                event["flow"] = {k: (compact(v, 4) if isinstance(v, float) else v) for k, v in flow.items()
                                 if k in ("gap_return", "min_bar_return", "min_bar_at", "max_bar_return", "max_bar_at", "max_drawdown",
                                          "max_drawdown_at", "net_buy_share", "big_print_share", "avg_trade_value", "at_low_limit_snaps",
                                          "at_high_limit_snaps", "low_limit_queue_max", "high_limit_queue_max", "close_at_low_limit")}
            day_events.append(event)
        windows = windows_cache[day]
        macro_context = []
        if windows:
            start, end = windows["pre_open"][0], windows["intraday"][1]
            macro_context = [{"id": str(n["id"]), "title": n["title"][:70], "source": n["source"], "url": n["url"], "time": iso(n["ts"]), "regulator": n["regulator"], "macro": n["macro"]}
                             for n in evidence["news_macro"].between("all", start, end)][:12]
        day_market = market_by_day.get(day, {})
        # Articles repeat across events of the same industry; store each once per shard.
        news_dict = {}

        def ref(item):
            key = str(item["id"])
            news_dict.setdefault(key, {"title": item["title"][:70], "source": item.get("source"), "url": item.get("url"),
                                       "time": item["time"], "regulator": bool(item.get("regulator"))})
            out = {"id": key, "window": item["window"]}
            if item.get("direction"):
                out["direction"] = item["direction"]
            return out
        by_level = defaultdict(list)
        for event in day_events:
            ev = event["ev"]
            ev["news_company"] = [ref(i) for i in ev["news_company"]]
            ev["news_industry"] = [ref(i) for i in ev["news_industry"]]
            for anno in ev["announcements"]:
                anno["title"] = anno["title"][:60]
            by_level[event["t"]].append(event)
        levels = {}
        for level in THRESHOLDS:
            members_at_level = by_level.get(level, [])
            used = {r["id"] for e in members_at_level for r in e["ev"]["news_company"] + e["ev"]["news_industry"]}
            shard = {"schema_version": 1, "date": day, "level": level, "available_at": day_market.get("available_at"),
                     "market": {k: day_market.get(k) for k in ("sample_median_return", "baseline_n", "screened_event_rate", "raw_events", "screened_events")},
                     "macro_news": macro_context, "news": {k: news_dict[k] for k in sorted(used)}, "events": members_at_level}
            result = study.write_json(out_dir / "events" / f"{day}-{int(round(level * 100))}.json", shard)
            levels[str(level)] = {**result, "events": len(members_at_level)}
        shard_manifest[str(day)] = {"events": len(day_events), "levels": levels}
    feature_labels = {}
    for table in calibrations.values():
        for feature in table["features"]:
            feature_labels.setdefault(feature, feature_label(feature))
    index = {
        "schema_version": 1,
        "metadata": {
            "version": "attribution-v1", "calculation_type": "retrospective_in_sample_association",
            "timezone": "Asia/Shanghai", "thresholds": list(THRESHOLDS), "boundary_tolerance": TOLERANCE,
            "source_daily_sha256": sha(daily), "builder_sha256": sha(__file__),
            "taxonomy_sha256": sha(Path(__file__).with_name("attribution_taxonomy.py")),
            "evidence_counts": evidence["counts"], "intraday_rows": len(intraday), "moneyflow_rows": len(moneyflow),
            "moneyflow": "Provider daily money-flow splits (small/medium/large/extra-large orders by trade value); main = large + extra-large. Thresholds: main net flow at or beyond 10% of turnover, extra-large at or beyond 5%, retail absorbing = main net outflow with small-plus-medium net inflow of at least 5%. Shares are of the day's turnover value; they describe observed order sizes, not identified participants.",
            "calendar": {"days": calendar.days, "assumption": "Sample dates plus weekdays 2026-06-22..30 and 2026-08-03 treated as trading days for window construction."},
            "windows": WINDOW_LABELS, "min_industry_peers": MIN_INDUSTRY_PEERS, "smoothing": SMOOTHING, "flow_floor": FLOW_FLOOR,
            "decomposition": "return = same-date screened sample median (market) + same-industry leave-one-out median minus market (industry) + stock-specific remainder; shares are clipped same-sign fractions.",
            "scoring": "Stock-specific share is divided among direction-consistent present features in proportion to the attributable fraction among the exposed, 1 - 1/LR with LR = P(feature|event)/P(feature|no event) at the event's threshold and direction, shrunk linearly below MIN_SUPPORT observations; features with LR below MIN_LR are listed as weak evidence without credit; trading-flow evidence always receives a floor so every event has a stated reason.",
            "min_lr": MIN_LR,
            "shard_keys": {"r": "return", "t": "threshold level", "m": "market (sample median) return", "i": "industry leave-one-out median return",
                           "peers": "industry peers used", "breadth": "share of other screened stocks down more than 5%", "vr": "volume ratio",
                           "sr": "spread ratio", "ic": "imbalance change", "shares": "[market, industry, stock-specific]",
                           "causes": "list of {f: feature, p: probability, w: weight, lr, er: event rate given feature, n: events with feature}",
                           "ev": "evidence items: announcements inline; news_company and news_industry reference ids in the shard's news dictionary", "flow": "intraday order-flow summary when available",
                           "mf": "provider daily money flow in yuan/shares: netFlow, mainFlow (large+extra-large), smainFlow (small+medium), netFlowS/M/L/XL, inflow/outflow, mainInflow/mainOutflow, turnoverValue, turnoverVol, dealAmount"},
            "shard_files": "events/{date}-{level}.json holds the events whose strongest threshold level is 2, 3 or 5 percent; a view at 3 percent needs the 3 and 5 files, a view at 2 percent needs all three.",
            "limitations": [
                "概率是 2026 年 7 月样本内的经验关联，不是因果证明，也未在样本外验证。",
                "公告 99% 以上在收盘后发布，公告类原因主要通过前一交易日收盘后至当日开盘前的窗口起作用；当日收盘后的公告只作为事后解释列出。",
                "新闻来源为财经类公众号文章标题与关联股票标签，关键词识别行业与政策，可能漏判或误判。",
                "行业归属来自公告元数据中的行业标签；无公告的股票没有行业层。",
                "成交与盘口证据来自快照代理指标（tick 规则、笔均额），不能识别交易者身份。",
                "日度观察在当日 16:00 后完整；本页不声称盘中可预测。",
            ],
        },
        "families": {k: {"label": v[0], "default_direction": v[1], "informational": v[2]} for k, v in FAMILIES.items()},
        "feature_labels": feature_labels, "window_labels": WINDOW_LABELS, "flow_labels": FLOW_LABELS,
        "industry_keywords": {k: list(v) for k, v in INDUSTRY_KEYWORDS.items()},
        "calibration": calibrations,
        "summary": {"stock_days": len(rows), "population": len(population), "events_by_key": dict(counts),
                    "events_total": sum(counts.values()), "strict_down5_events": len(strict_events),
                    "event_keys": len(event_keys),
                    "stocks_with_industry": len(stock_industry), "stocks_with_name": len(evidence["stock_name"]),
                    "industry_medians": len(ind_med), "dates": sorted(by_date)},
        "stocks": {code: {"name": evidence["stock_name"].get(code), "industry": stock_industry.get(code)}
                   for code in sorted({r["symbol"] for r in rows})},
        "shards": shard_manifest, "market_by_date": market,
    }
    result = study.write_json(out_dir / "index.json", index)
    audit = {"index": result, "shards": shard_manifest, "summary": index["summary"], "metadata": index["metadata"]}
    study.write_json(ROOT / ".runtime" / "attribution-audit.json", audit)
    return index["summary"]


def _round_cause(c):
    out = {}
    for k, v in c.items():
        if k == "signals":
            out[k] = [_round_cause(s) for s in v]
        elif isinstance(v, float):
            out[k] = compact(v, 6)
        else:
            out[k] = v
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--intraday", type=Path)
    parser.add_argument("--moneyflow", type=Path, help="Directory of daily provider money-flow JSON files; not published")
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "attribution")
    args = parser.parse_args()
    out = args.out.resolve()
    if ROOT not in out.parents and out != ROOT:
        parser.error("Output directory must be inside the site checkout")
    summary = build(args.daily, args.evidence, args.intraday, out, args.moneyflow)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
