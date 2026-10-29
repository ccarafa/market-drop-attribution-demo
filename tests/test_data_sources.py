"""Tests for the bounded data-source checks and the sources manifest builder."""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import check_data_sources as cds  # noqa: E402

TOKEN = "a" * 64
S3 = "b" * 32


def transport_factory(responses, log):
    def transport(method, url, headers, body=None, timeout=30):
        log.append({"method": method, "url": url, "headers": dict(headers)})
        for key, (status, payload) in responses.items():
            if key in url:
                return status, payload
        return 500, b"{}"
    return transport


class CheckDataSourcesTests(unittest.TestCase):
    def test_all_routes_connected_with_one_request_each(self):
        log = []
        row = json.dumps({"retCode": 1, "data": [{"ticker": "000001", "closePrice": 1.0}]}).encode()
        transport = transport_factory({"getMktEqud.json": (200, row), "getMktEquMfNew.json": (200, row),
                                       "bigdata-s3-data": (200, b""), "dataapi-mcp-server": (200, b'{"result":{"serverInfo":{}}}')}, log)
        report = cds.check_sources("2026-07-15", "000001", environ={"DATAYES_API_TOKEN": TOKEN, "DATAYES_S3_TOKEN": S3}, transport=transport)
        self.assertEqual(report["overall"], "connected")
        self.assertEqual([p["status"] for p in report["probes"]], ["connected_sample"] * 4)
        self.assertEqual(len(log), 4)
        self.assertEqual(log[1]["headers"]["Authorization"], "Bearer " + TOKEN)
        self.assertEqual(log[2]["method"], "HEAD")
        text = json.dumps(report)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(S3, text)
        self.assertIsNone(re.search(r"[0-9a-f]{40,}", text.replace(report["probes"][0]["response_row_sha256"], "")))

    def test_missing_tokens_make_no_requests(self):
        log = []
        report = cds.check_sources("2026-07-15", "000001", environ={}, transport=transport_factory({}, log))
        self.assertEqual(log, [])
        self.assertEqual(report["overall"], "configuration_required")
        self.assertTrue(all(p["status"] == "configuration_required" for p in report["probes"]))

    def test_entitlement_and_transport_failures_are_categorised(self):
        log = []
        transport = transport_factory({"getMktEqud.json": (200, b'{"retCode":-403,"retMsg":"Need Privilege"}'), "getMktEquMfNew.json": (403, b"forbidden"),
                                       "bigdata-s3-data": (None, b""), "dataapi-mcp-server": (401, b"{}")}, log)
        report = cds.check_sources("2026-07-15", "000001", environ={"DATAYES_API_TOKEN": TOKEN, "DATAYES_S3_TOKEN": S3}, transport=transport)
        self.assertEqual([p["status"] for p in report["probes"]], ["authentication_failed", "entitlement_required", "transport_failed", "entitlement_required"])
        self.assertEqual(report["overall"], "attention")
        self.assertNotIn("Need Privilege", json.dumps(report))

    def test_invalid_scope_rejected(self):
        with self.assertRaises(ValueError):
            cds.check_sources("20260715", "000001", environ={}, transport=transport_factory({}, []))


class SourcesManifestTests(unittest.TestCase):
    def test_published_manifest_has_no_secrets_and_expected_shape(self):
        path = Path(__file__).resolve().parents[1] / "data" / "sources.json"
        if not path.is_file():
            self.skipTest("sources manifest not built")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertGreaterEqual(len(data["sources"]), 5)
        self.assertTrue(all(key in s for s in data["sources"] for key in ("name", "route", "auth", "use", "status")))
        self.assertEqual({c["name"] for c in data["credentials"]}, {"DATAYES_API_TOKEN", "DATAYES_S3_TOKEN"})
        self.assertIn("settings/secrets/actions", data["links"]["secrets"])
        text = path.read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"\b[0-9a-f]{32}\b", text.replace(data["build"].get("attribution_builder_sha256") or "", "")
                                     .replace(json.dumps(data.get("last_check") or {}), "")))


if __name__ == "__main__":
    unittest.main()
