"""Synthetic temporal/numerical checks; no private source or pandas required."""
import copy
from datetime import date, timedelta
import importlib.util
import json
import math
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_real_study", ROOT / "tools" / "build_real_study.py")
study = importlib.util.module_from_spec(spec)
spec.loader.exec_module(study)


def source(symbol="000001", day=20260701, ret=-0.06, price=94.0, previous=100.0, volume=100.0, spread=2.0, imbalance=0.3, snapshot=None):
    iso = f"{day // 10000:04}-{day // 100 % 100:02}-{day % 100:02}"
    return {"symbol": symbol, "date": str(day), "daily_return_proxy": ret,
            "last_price_proxy": price, "prev_close": previous,
            "volume_final_shares": volume, "spread_bps_mean": spread,
            "abs_imbalance_mean": imbalance, "last_datetime": snapshot or iso + " 15:40:00.000"}


class NumericalAndTemporalTests(unittest.TestCase):
    def test_strict_threshold(self):
        self.assertFalse(study.is_drop(-0.05))
        self.assertFalse(study.is_drop(-0.050000000000000044))
        self.assertFalse(study.is_drop(-0.05 - 0.5e-12))
        self.assertTrue(study.is_drop(-0.0500001))
        for value in (None, float("nan"), float("inf"), "bad", True):
            self.assertFalse(study.is_drop(value))

    def test_leave_one_out_ties_ineligible_and_singleton(self):
        rows = [{"_key": str(i), "return": v, "ok": ok} for i, (v, ok) in enumerate([
            (-0.10, True), (-0.10, True), (0.0, True), (0.20, True), (-0.99, False)])]
        actual = study.leave_one_out(rows, lambda r: r["ok"])
        self.assertEqual(actual["0"], (0.0, 3, 1 / 3))
        self.assertEqual(actual["2"], (-0.10, 3, 2 / 3))
        self.assertEqual(actual["4"], (-0.05, 4, 0.5))
        self.assertEqual(study.leave_one_out(rows[:1], lambda r: True)["0"], (None, 0, None))
        self.assertEqual(study.leave_one_out(rows, lambda r: False)["0"], (None, 0, None))

    def test_missing_and_invalid_values_do_not_become_zero(self):
        records = [source(ret=float("nan")), source("000002", price=0), source("000003", volume=-1, spread=-1, imbalance=2)]
        rows, events, _, summary = study.compute_study(records)
        self.assertEqual(summary["raw_events"], 2)
        self.assertIsNone(rows[0]["return"])
        self.assertFalse(rows[0]["screened"])
        self.assertFalse(rows[1]["screened"])
        self.assertIn("invalid_price", rows[1]["flags"])
        self.assertIsNone(rows[2]["volume_ratio"])
        self.assertIn("invalid_volume", rows[2]["flags"])
        self.assertIn("invalid_spread", rows[2]["flags"])
        self.assertIn("invalid_imbalance", rows[2]["flags"])
        json.dumps([study.array(r) for r in rows], allow_nan=False)

    def test_prior_five_shift_missing_and_future_perturbation(self):
        records = []
        for i in range(8):
            records.append(source(day=20260701 + i, ret=0, price=100, volume=10 * (i + 1), spread=i + 1, imbalance=(i + 1) / 10))
        rows, _, _, _ = study.compute_study(records)
        self.assertEqual(rows[4]["volume_prior5_count"], 4)
        self.assertIsNone(rows[4]["volume_prior5_median"])
        self.assertEqual(rows[5]["volume_prior5_median"], 30)
        self.assertEqual(rows[5]["volume_ratio"], 2)
        self.assertAlmostEqual(rows[5]["imbalance_change"], 0.3)
        changed = copy.deepcopy(records)
        changed[6]["volume_final_shares"] = 10**12
        changed[7]["daily_return_proxy"] = -0.9
        later, _, _, _ = study.compute_study(changed)
        self.assertEqual([study.array(r) for r in rows[:6]], [study.array(r) for r in later[:6]])
        changed = copy.deepcopy(records)
        changed[2]["volume_final_shares"] = None
        missing, _, _, _ = study.compute_study(changed)
        self.assertEqual(missing[5]["volume_prior5_count"], 4)
        self.assertIsNone(missing[5]["volume_ratio"])

    def test_screening_retains_events_and_unknown_reference(self):
        records = [source(ret=0, price=100), source(day=20260702, ret=-0.10, price=81, previous=90), source("000002", ret=0.8, price=180)]
        rows, events, _, summary = study.compute_study(records)
        self.assertTrue(rows[0]["screened"])
        self.assertIn("reference_history_missing", rows[0]["flags"])
        self.assertEqual(len(events), 1)
        self.assertFalse(events[0]["screened"])
        self.assertIn("reference_gap_gt_1pct", events[0]["flags"])
        self.assertEqual(summary["screened_events"], 0)
        self.assertIn("extreme_return_gt_50pct", rows[-1]["flags"])

    def test_availability_accounts_for_late_peer_snapshot(self):
        rows, _, market, _ = study.compute_study([source(), source("000002", snapshot="2026-07-01 16:20:00.000")])
        self.assertTrue(all(r["available_at"] == "2026-07-01T16:20:00+08:00" for r in rows))
        self.assertEqual(market[0]["available_at"], rows[0]["available_at"])
        rows, _, _, _ = study.compute_study([source()])
        self.assertEqual(rows[0]["available_at"], "2026-07-01T16:00:00+08:00")

    def test_duplicate_keys_rejected_and_input_order_independent(self):
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            study.compute_study([source(), source()])
        records = [source("000003"), source("000001"), source("000002")]
        a, _, _, _ = study.compute_study(records)
        b, _, _, _ = study.compute_study(list(reversed(records)))
        self.assertEqual([study.array(r) for r in a], [study.array(r) for r in b])


@unittest.skipUnless((ROOT / "data" / "study.json").is_file(), "Committed study artifact unavailable")
class CommittedArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads((ROOT / "data" / "study.json").read_text(encoding="utf-8"))
        cls.events = [dict(zip(cls.payload["columns"], row)) for row in cls.payload["events"]]

    def test_event_count_unique_keys_and_strict_reconstruction(self):
        self.assertEqual(self.payload["summary"]["raw_events"], 13246)
        self.assertEqual(len(self.events), 13246)
        self.assertEqual(len({(r["symbol"], r["date"]) for r in self.events}), 13246)
        self.assertEqual(sum(r["screened"] for r in self.events), self.payload["summary"]["screened_events"])
        for r in self.events:
            self.assertTrue(study.is_drop(r["return"]))
            self.assertAlmostEqual(r["last_price"] / r["prev_close"] - 1, r["return"], places=10)
            self.assertGreaterEqual(r["available_at"][11:19], "16:00:00")
            for kind in ("volume", "spread", "imbalance"):
                if r[f"{kind}_prior5_count"] < 5:
                    self.assertIsNone(r[f"{kind}_prior5_median"])

    def test_histories_cover_all_events_and_market_counts(self):
        histories = {}
        for prefix in self.payload["series"]["shards"]:
            path = ROOT / "data" / "series" / f"{prefix}.json"
            shard = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(shard["columns"], study.HISTORY_COLUMNS)
            for symbol, rows in shard["symbols"].items():
                self.assertEqual(symbol[0], prefix)
                for row in rows:
                    r = dict(zip(shard["columns"], row))
                    key = (symbol, r["date"])
                    self.assertNotIn(key, histories)
                    histories[key] = row
        self.assertEqual(len(histories), 119652)
        for row in self.payload["events"]:
            r = dict(zip(self.payload["columns"], row))
            self.assertEqual(study.history_array(r), histories[(r["symbol"], r["date"])])
        self.assertEqual(sum(d["raw_events"] for d in self.payload["market_by_date"]), 13246)


if __name__ == "__main__":
    unittest.main()
