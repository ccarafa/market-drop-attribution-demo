"""Official-client contract tests: synthetic token and mocked responses only."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("datayes_client_test_module", ROOT / "tools" / "datayes_client.py")
api = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = api
spec.loader.exec_module(api)
MOCK_TOKEN = "synthetic-token-for-offline-tests"
FIELDS = ("ticker", "exchangeCD", "tradeDate", "closePrice", "turnoverVol", "isOpen")


def quote(ticker="000001", day="2025-10-15", price=10.0):
    return {"ticker": ticker, "exchangeCD": "XSHE", "tradeDate": day,
            "closePrice": price, "turnoverVol": 100.0, "isOpen": 1}


class FakeTransport:
    def __init__(self, pages):
        self.pages = iter(pages)
        self.calls = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls.append((url, headers, timeout, max_bytes))
        value = next(self.pages)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(url)
        return api.HttpResponse(200, json.dumps({"retCode": 1, "data": value}).encode(), {}, url)


def client(pages, **limits):
    transport = FakeTransport(pages)
    config = api.Limits(page_size=2, min_interval_seconds=0, **limits)
    return api.DataYesClient(MOCK_TOKEN, transport=transport, limits=config), transport


class ClientTests(unittest.TestCase):
    def test_missing_token_fails_without_transport_or_environment_fallback(self):
        transport = FakeTransport([])
        for environment in ({}, {"DATAYES_PASSWORD": "not-an-api-token"}, {api.TOKEN_ENV: ""}):
            with self.assertRaises(api.MissingTokenError):
                api.DataYesClient.from_environment(environ=environment, transport=transport)
        self.assertEqual(transport.calls, [])
        with self.assertRaises(api.MissingTokenError):
            api.DataYesClient("synthetic\r\nheader", transport=transport)

    def test_explicit_query_fields_lowercase_pagination_and_terminal_empty(self):
        c, transport = client([[quote(), quote("000002")], []])
        result = c.fetch_daily(["20251015"], ["000002", "000001"], fields=FIELDS)
        self.assertEqual(len(result.rows), 2)
        self.assertEqual(result.request_count, 2)
        queries = [parse_qs(urlsplit(call[0]).query) for call in transport.calls]
        self.assertEqual(queries[0], {"tradeDate": ["20251015"], "field": [",".join(FIELDS)], "pagenum": ["1"], "pagesize": ["2"], "ticker": ["000001,000002"]})
        self.assertEqual(queries[1]["pagenum"], ["2"])
        self.assertEqual(transport.calls[0][1]["Authorization"], "Bearer " + MOCK_TOKEN)
        self.assertNotIn(MOCK_TOKEN, transport.calls[0][0])
        self.assertFalse(result.full_database_download_complete)
        self.assertFalse(result.dates[0]["completeness_verified"])
        self.assertFalse(result.dates[0]["pagination_convention_verified"])
        self.assertEqual(result.dates[0]["first_page"], 1)
        self.assertEqual(result.dates[0]["terminal_page"], "empty")

    def test_full_market_is_explicit_and_short_page_is_not_proof(self):
        c, transport = client([[quote()]])
        with self.assertRaises(api.RequestValidationError):
            c.fetch_daily(["20251015"], None, fields=FIELDS)
        self.assertEqual(len(transport.calls), 0)
        result = c.fetch_daily(["20251015"], None, fields=FIELDS, full_market=True)
        self.assertNotIn("ticker", parse_qs(urlsplit(transport.calls[0][0]).query))
        self.assertEqual(result.completeness_status, "pagination_exhausted_unverified")

    def test_duplicate_pages_and_overlap_rejected(self):
        for pages in ([[quote(), quote("000002")], [quote(), quote("000002")]],
                      [[quote(), quote("000002")], [quote("000002", price=12.0)]],
                      [[quote(), quote()]]):
            c, _ = client(pages)
            with self.assertRaises(api.IncompleteDownloadError):
                c.fetch_daily(["20251015"], None, fields=FIELDS, full_market=True)

    def test_page_request_row_and_date_budgets(self):
        scenarios = [({"max_pages_per_date": 1}, [[quote(), quote("000002")]], 1),
                     ({"max_requests": 1}, [[quote(), quote("000002")]], 1),
                     ({"max_rows": 1}, [[quote(), quote("000002")]], 1)]
        for limits, pages, expected_calls in scenarios:
            c, t = client(pages, **limits)
            with self.assertRaises(api.BudgetExceededError):
                c.fetch_daily(["20251015"], None, fields=FIELDS, full_market=True)
            self.assertEqual(len(t.calls), expected_calls)
        c, t = client([], max_dates=1)
        with self.assertRaises(api.RequestValidationError):
            c.fetch_daily(["20251014", "20251015"], ["000001"], fields=FIELDS)
        self.assertEqual(t.calls, [])

    def test_request_validation_prevents_network_calls(self):
        c, t = client([])
        bad = [(["20250230"], ["000001"], FIELDS), (["20251015", "2025-10-15"], ["000001"], FIELDS),
               (["20251015"], ["000001", "000001"], FIELDS), (["20251015"], [1], FIELDS),
               (["20251015"], ["000001"], ["closePrice"]), (["20251015"], ["000001"], FIELDS + ("bad&field",))]
        for dates, tickers, fields in bad:
            with self.assertRaises(api.RequestValidationError):
                c.fetch_daily(dates, tickers, fields=fields)
        self.assertEqual(t.calls, [])

    def test_strict_response_and_row_contract(self):
        payloads = [
            {"retCode": True, "data": []}, {"retCode": "1", "data": []}, {"retCode": 0, "retMsg": MOCK_TOKEN, "data": []},
            {"retCode": 1, "data": {}}, {"retCode": 1, "data": [{}]},
            {"retCode": 1, "data": [quote(day="2025-10-14")]},
            {"retCode": 1, "data": [quote("000002")]},
            {"retCode": 1, "data": [quote(price=-1)]},
            {"retCode": 1, "data": [{**quote(), "closePrice": True}]},
            {"retCode": 1, "data": [{**quote(), "isOpen": True}]},
            {"retCode": 1, "data": [{**quote(), "turnoverVol": -1}]},
        ]
        for payload in payloads:
            c, _ = client([lambda url, p=payload: api.HttpResponse(200, json.dumps(p).encode(), {}, url)])
            with self.assertRaises(api.ResponseValidationError) as caught:
                c.fetch_daily(["20251015"], ["000001"], fields=FIELDS)
            self.assertNotIn(MOCK_TOKEN, str(caught.exception))

    def test_redirects_http_auth_rate_and_oversized_responses(self):
        scenarios = [
            (lambda url: api.HttpResponse(302, b"", {"Location": "https://example.invalid/"}, url), api.ResponseValidationError),
            (lambda url: api.HttpResponse(200, b"{}", {}, "https://example.invalid/"), api.ResponseValidationError),
            (lambda url: api.HttpResponse(401, MOCK_TOKEN.encode(), {}, url), api.AuthenticationError),
            (lambda url: api.HttpResponse(403, MOCK_TOKEN.encode(), {}, url), api.EntitlementError),
            (lambda url: api.HttpResponse(503, MOCK_TOKEN.encode(), {}, url), api.TransportError),
            (lambda url: api.HttpResponse(200, b"x" * 101, {}, url), api.ResponseValidationError),
            (lambda url: api.HttpResponse(200, b'{"retCode":1,"data":[NaN]}', {}, url), api.ResponseValidationError),
            (lambda url: api.HttpResponse(200, b'{"retCode":0,"retCode":1,"data":[]}', {}, url), api.ResponseValidationError),
        ]
        for response, kind in scenarios:
            c, t = client([response], max_response_bytes=100)
            with self.assertRaises(kind) as caught:
                c.fetch_daily(["20251015"], ["000001"], fields=FIELDS)
            self.assertEqual(len(t.calls), 1)
            self.assertNotIn(MOCK_TOKEN, str(caught.exception))
        c, t = client([lambda url: api.HttpResponse(429, MOCK_TOKEN.encode(), {"Retry-After": "120"}, url)])
        with self.assertRaises(api.RateLimitError) as caught:
            c.fetch_daily(["20251015"], ["000001"], fields=FIELDS)
        self.assertEqual(caught.exception.retry_after_seconds, 120)
        self.assertEqual(len(t.calls), 1)

    def test_errors_repr_and_manifests_do_not_disclose_token(self):
        c, _ = client([RuntimeError("header Bearer " + MOCK_TOKEN)])
        with self.assertRaises(api.TransportError) as caught:
            c.fetch_daily(["20251015"], ["000001"], fields=FIELDS)
        self.assertNotIn(MOCK_TOKEN, str(caught.exception))
        self.assertNotIn(MOCK_TOKEN, repr(c))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assertIsNone(caught.exception.__context__)
        c, _ = client([[quote()]])
        result = c.fetch_daily(["20251015"], ["000001", "000002"], fields=FIELDS)
        self.assertNotIn(MOCK_TOKEN, json.dumps(result.manifest()))
        self.assertEqual(result.dates[0]["missing_requested_tickers"], ["000002"])

    def test_rate_spacing_timeout_and_lifetime_request_budget(self):
        current = [100.0]
        sleeps = []
        def sleep(duration):
            sleeps.append(duration)
            current[0] += duration
        t = FakeTransport([[quote()], [quote(day="2025-10-16")]])
        c = api.DataYesClient(MOCK_TOKEN, transport=t, limits=api.Limits(page_size=2, max_requests=2, timeout_seconds=7, min_interval_seconds=0.5), clock=lambda: current[0], sleep=sleep)
        c.fetch_daily(["20251015"], ["000001"], fields=FIELDS)
        c.fetch_daily(["20251016"], ["000001"], fields=FIELDS)
        self.assertEqual(sleeps, [0.5])
        self.assertTrue(all(call[2] == 7 for call in t.calls))
        with self.assertRaises(api.BudgetExceededError):
            c.fetch_daily(["20251017"], ["000001"], fields=FIELDS)
        self.assertEqual(len(t.calls), 2)

    def test_null_measurements_remain_null_and_extra_fields_are_not_returned(self):
        c, _ = client([[{**quote(), "closePrice": None, "unrequested": "discard"}]])
        result = c.fetch_daily(["20251015"], ["000001"], fields=FIELDS)
        self.assertIsNone(result.rows[0]["closePrice"])
        self.assertNotIn("unrequested", result.rows[0])

    def test_documented_body_error_codes_are_typed_without_reflected_secrets(self):
        cases = [(-403, api.AuthenticationError, "authentication_failed"),
                 (-1403, api.EntitlementError, "entitlement_required"),
                 (402, api.QuotaError, "quota_exhausted"),
                 (409, api.QuotaError, "quota_exhausted"),
                 (429, api.RateLimitError, "rate_limited")]
        for field in ("retCode", "code"):
            for code, kind, category in cases:
                payload = {field: code, "retMsg": "Bearer " + MOCK_TOKEN,
                           "message": MOCK_TOKEN, "data": [quote()]}
                c, t = client([lambda url, p=payload: api.HttpResponse(200, json.dumps(p).encode(), {}, url)])
                with self.assertRaises(kind) as caught:
                    c.fetch_daily(["20251015"], ["000001"], fields=FIELDS)
                self.assertEqual(caught.exception.error_category, category)
                self.assertEqual(caught.exception.provider_code, code)
                self.assertEqual(caught.exception.http_status, 200)
                self.assertNotIn(MOCK_TOKEN, str(caught.exception))
                self.assertNotIn(MOCK_TOKEN, repr(vars(caught.exception)))
                self.assertIsNone(caught.exception.__context__)
                self.assertEqual(len(t.calls), 1)

    def test_http_quota_entitlement_auth_and_rate_categories(self):
        cases = [(401, api.AuthenticationError, "authentication_failed"),
                 (403, api.EntitlementError, "entitlement_required"),
                 (402, api.QuotaError, "quota_exhausted"),
                 (409, api.QuotaError, "quota_exhausted"),
                 (429, api.RateLimitError, "rate_limited")]
        for status, kind, category in cases:
            c, _ = client([lambda url, status=status: api.HttpResponse(status, MOCK_TOKEN.encode(), {}, url)])
            with self.assertRaises(kind) as caught:
                c.fetch_daily(["20251015"], ["000001"], fields=FIELDS)
            self.assertEqual(caught.exception.error_category, category)
            self.assertEqual(caught.exception.http_status, status)
            self.assertIsNone(caught.exception.provider_code)
            self.assertNotIn(MOCK_TOKEN, str(caught.exception))

    def test_known_body_error_refines_ambiguous_http_forbidden(self):
        payload = {"retCode": -403, "retMsg": MOCK_TOKEN}
        c, _ = client([lambda url: api.HttpResponse(403, json.dumps(payload).encode(), {}, url)])
        with self.assertRaises(api.AuthenticationError) as caught:
            c.fetch_daily(["20251015"], ["000001"], fields=FIELDS)
        self.assertEqual(caught.exception.error_category, "authentication_failed")
        c, _ = client([lambda url: api.HttpResponse(200, json.dumps({"code": 200, "data": [quote()]}).encode(), {}, url)])
        with self.assertRaises(api.ResponseValidationError):
            c.fetch_daily(["20251015"], ["000001"], fields=FIELDS)


if __name__ == "__main__":
    unittest.main()
