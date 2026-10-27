"""Unit tests for the attribution taxonomy and builder; standard library only."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import attribution_taxonomy as tax  # noqa: E402
import build_attribution as ba  # noqa: E402


class TaxonomyTests(unittest.TestCase):
    def test_fine_label_wins_and_direction_cues(self):
        family, direction, informational = tax.classify_announcement("减持-股权变动", "重大事项-股份增减持", "某公司:控股股东减持股份结果的公告")
        self.assertEqual((family, direction, informational), ("share_reduction", -1, True))
        family, direction, _ = tax.classify_announcement("减持-股权变动", "", "某公司:关于董事增持股份计划的公告")
        self.assertEqual((family, direction), ("share_increase", +1))

    def test_coarse_atom_fallback_and_routine_families(self):
        family, _, informational = tax.classify_announcement("其他", "交易提示-交易异动", "某公司:股票交易异常波动公告")
        self.assertEqual((family, informational), ("abnormal_trading", True))
        family, _, informational = tax.classify_announcement("股东大会-日常经营", "一般公告-股东大会公告", "某公司:2026年第一次临时股东会资料")
        self.assertEqual((family, informational), ("governance", False))

    def test_risk_warning_withdrawal_is_positive(self):
        family, direction, _ = tax.classify_announcement("风险警示-交易提示", "交易提示-风险提示", "ST某某:关于申请撤销公司股票交易其他风险警示的公告")
        self.assertEqual((family, direction), ("risk_warning", +1))
        _, direction, _ = tax.classify_announcement("风险警示-交易提示", "", "*ST某某:关于公司股票可能被终止上市的风险提示公告")
        self.assertEqual(direction, -1)

    def test_news_tags(self):
        tags = tax.classify_news("证监会就减持新规征求意见，光伏板块承压", ["600001"], {"600001": "银行"})
        self.assertTrue(tags["regulator"])
        self.assertIn("电力设备", tags["industries"])
        self.assertIn("银行", tags["industries"])
        self.assertEqual(tags["tickers"], ["600001"])
        self.assertEqual(tax.classify_news("美联储加息预期升温", [], {})["macro"], True)


class WindowTests(unittest.TestCase):
    def test_windows_follow_trading_calendar(self):
        calendar = ba.Calendar([20260701, 20260702, 20260703, 20260706])
        w = calendar.windows(20260701)
        self.assertEqual(w["pre_open"], (ba.ms_at(20260630, 15, 0), ba.ms_at(20260701, 9, 30)))
        self.assertEqual(w["post_close"], (ba.ms_at(20260701, 15, 0), ba.ms_at(20260702, 9, 30)))
        self.assertEqual(w["prior"], (ba.ms_at(20260624, 15, 0), ba.ms_at(20260630, 15, 0)))
        self.assertIsNone(calendar.windows(20260622))

    def test_index_slicing_is_left_open_right_closed(self):
        index = ba.Index()
        for ts in (10, 20, 30):
            index.add("k", ts, {"ts": ts})
        index.finish()
        self.assertEqual([i["ts"] for i in index.between("k", 10, 30)], [20, 30])
        self.assertEqual(index.between("missing", 0, 100), [])


class ScoringTests(unittest.TestCase):
    def make_population(self):
        rows = []
        for i in range(400):
            event = i < 100
            feats = set()
            if event and i % 2 == 0 or (not event and i % 20 == 0):
                feats.add("anno:share_reduction:neg:pre_open")
            if event and i % 4 == 0 or (not event and i % 4 == 1):
                feats.add("anno:buyback:pos:pre_open")
            rows.append({"return": -0.08 if event else -0.01, "_features": feats})
        return rows

    def test_calibration_counts_and_ratio(self):
        table = ba.calibrate(self.make_population(), -1, 0.05)
        self.assertEqual((table["events"], table["others"]), (100, 300))
        entry = table["features"]["anno:share_reduction:neg:pre_open"]
        self.assertEqual((entry["events_with"], entry["others_with"]), (50, 15))
        self.assertGreater(entry["likelihood_ratio"], 5)
        self.assertAlmostEqual(entry["p_given_event"] - entry["p_given_other"], entry["excess"])

    def test_scores_conserve_probability_mass_and_respect_direction(self):
        table = ba.calibrate(self.make_population(), -1, 0.05)
        row = {"return": -0.10, "sample_median_return": -0.02, "_industry_median": -0.05,
               "_features": {"anno:share_reduction:neg:pre_open", "anno:buyback:pos:pre_open", "volume_ratio_ge2", "explain:earnings"}}
        scored = ba.score_event(row, table)
        self.assertEqual(scored["direction"], -1)
        self.assertAlmostEqual(scored["market_share"], 0.2)
        self.assertAlmostEqual(scored["industry_share"], 0.3)
        total = scored["market_share"] + scored["industry_share"] + sum(c["p"] for c in scored["causes"])
        self.assertAlmostEqual(total, 1.0)
        self.assertEqual([c["f"] for c in scored["contrary"]], ["anno:buyback:pos:pre_open"])
        self.assertEqual([c["f"] for c in scored["explanations"]], ["explain:earnings"])
        self.assertIn("flow", [c["f"] for c in scored["causes"]])
        flow = next(c for c in scored["causes"] if c["f"] == "flow")
        self.assertEqual([s["f"] for s in flow["signals"]], ["volume_ratio_ge2"])

    def test_flow_is_sole_cause_without_information(self):
        table = ba.calibrate(self.make_population(), -1, 0.05)
        row = {"return": -0.06, "sample_median_return": 0.001, "_industry_median": None, "_features": set()}
        scored = ba.score_event(row, table)
        self.assertEqual(len(scored["causes"]), 1)
        self.assertAlmostEqual(scored["causes"][0]["p"], 1.0)

    def test_threshold_level_is_strict(self):
        self.assertEqual(ba.threshold_level(-0.05), 0.03)
        self.assertEqual(ba.threshold_level(-0.0500001), 0.05)
        self.assertEqual(ba.threshold_level(-0.02), None)
        self.assertEqual(ba.threshold_level(0.021), 0.02)
        self.assertEqual(ba.threshold_level(-0.03 - 1e-9), 0.03)

    def test_feature_consistency(self):
        self.assertTrue(ba.feature_consistent("anno:share_reduction:neg:pre_open", -1))
        self.assertFalse(ba.feature_consistent("anno:share_reduction:neg:pre_open", +1))
        self.assertTrue(ba.feature_consistent("anno:earnings:neu:prior", +1))
        self.assertTrue(ba.feature_consistent("news_industry_regulator:neg:pre_open", -1))
        self.assertFalse(ba.feature_consistent("news_industry_regulator:neg:pre_open", +1))
        self.assertTrue(ba.feature_consistent("news_industry_regulator:neu:intraday", +1))
        self.assertTrue(ba.feature_consistent("limit_down_touched", -1))
        self.assertFalse(ba.feature_consistent("limit_down_touched", +1))
        self.assertFalse(ba.feature_consistent("explain:earnings", -1))


if __name__ == "__main__":
    unittest.main()
