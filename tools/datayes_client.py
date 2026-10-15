"""Bounded official DataYes daily API client; never uses dictionary passwords.

The API token is supplied explicitly or through DATAYES_API_TOKEN. This module
does not read credentials on import and makes no requests until fetch_daily.
No response is certified as a complete database: the documented API has no
total/count contract, so a terminal short page is only pagination evidence.
The default first page of 1 is an explicit client convention, not a documented
provider guarantee; callers can configure 0, and manifests retain this limit.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import os
import re
import ssl
import time
from typing import Callable, Mapping, Sequence
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


ENDPOINT = "https://gw.datayes.com/aladdin_proxy/data_api/api/market/getMktEqud.json"
METADATA_URL = "https://gw.datayes.com/aladdin_llm_mgmt/web/whitelist/api?nameEn=Astock_market_daily"
TOKEN_ENV = "DATAYES_API_TOKEN"
DEFAULT_FIELDS = (
    "ticker", "exchangeCD", "tradeDate", "preClosePrice", "actPreClosePrice",
    "closePrice", "turnoverVol", "turnoverValue", "chgPct", "accumAdjFactor", "isOpen",
)
IDENTITY_FIELDS = frozenset(("ticker", "exchangeCD", "tradeDate"))
NUMERIC_FIELDS = frozenset(DEFAULT_FIELDS) - IDENTITY_FIELDS - {"isOpen"}
PRICE_FIELDS = frozenset(("preClosePrice", "actPreClosePrice", "closePrice", "accumAdjFactor"))
NONNEGATIVE_FIELDS = frozenset(("turnoverVol", "turnoverValue"))


class DataYesError(RuntimeError):
    """Sanitized error: never includes URLs, tokens, headers, or provider text."""
    error_category = "provider_error"

    def __init__(self, message, *, provider_code=None, http_status=None):
        super().__init__(message)
        self.provider_code = provider_code
        self.http_status = http_status


class MissingTokenError(DataYesError):
    error_category = "missing_token"


class RequestValidationError(DataYesError):
    error_category = "invalid_request"


class AuthenticationError(DataYesError):
    error_category = "authentication_failed"


class EntitlementError(DataYesError):
    error_category = "entitlement_required"


class QuotaError(DataYesError):
    error_category = "quota_exhausted"


class TransportError(DataYesError):
    error_category = "transport_failed"


class ResponseValidationError(DataYesError):
    error_category = "invalid_response"


class IncompleteDownloadError(DataYesError):
    error_category = "incomplete_download"


class BudgetExceededError(IncompleteDownloadError):
    error_category = "budget_exceeded"


class RateLimitError(DataYesError):
    error_category = "rate_limited"

    def __init__(self, retry_after_seconds=None, *, provider_code=None, http_status=None):
        super().__init__("Provider rate limit reached; no automatic retry was made",
                         provider_code=provider_code, http_status=http_status)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True)
class Limits:
    max_dates: int = 20
    max_date_span_days: int = 366
    max_tickers: int = 100
    max_pages_per_date: int = 10
    max_requests: int = 100
    max_rows: int = 100_000
    page_size: int = 1000
    first_page: int = 1
    timeout_seconds: float = 30.0
    min_interval_seconds: float = 1.0
    max_response_bytes: int = 8 * 1024 * 1024

    def __post_init__(self):
        maximums = {
            "max_dates": 366, "max_date_span_days": 3660, "max_tickers": 10000,
            "max_pages_per_date": 1000, "max_requests": 10000,
            "max_rows": 1_000_000, "page_size": 10000,
            "max_response_bytes": 64 * 1024 * 1024,
        }
        for key, maximum in maximums.items():
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= maximum:
                raise RequestValidationError("Invalid bounded request limit")
        if type(self.first_page) is not int or self.first_page not in (0, 1):
            raise RequestValidationError("Invalid first page")
        if type(self.timeout_seconds) not in (float, int) or not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 120:
            raise RequestValidationError("Invalid request timeout")
        if type(self.min_interval_seconds) not in (float, int) or not math.isfinite(self.min_interval_seconds) or not 0 <= self.min_interval_seconds <= 60:
            raise RequestValidationError("Invalid request interval")


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]
    final_url: str


@dataclass(frozen=True)
class FetchResult:
    rows: tuple[dict, ...]
    dates: tuple[dict, ...]
    fields: tuple[str, ...]
    request_count: int
    source: str = "DataYes Astock_market_daily"
    completeness_status: str = "pagination_exhausted_unverified"
    full_database_download_complete: bool = False

    def manifest(self):
        """Credential-free metadata; callers decide separately where to save rows."""
        return {
            "source": self.source, "metadata_url": METADATA_URL,
            "fields": list(self.fields), "rows": len(self.rows),
            "request_count": self.request_count, "dates": list(self.dates),
            "completeness_status": self.completeness_status,
            "full_database_download_complete": self.full_database_download_complete,
        }


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward an Authorization header through any redirect.
        return None


def urllib_transport(url: str, headers: Mapping[str, str], timeout: float, max_bytes: int) -> HttpResponse:
    """Default TLS-verifying GET transport. Responses are bounded before parsing."""
    opener = build_opener(_RejectRedirects(), HTTPSHandler(context=ssl.create_default_context()))
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with opener.open(request, timeout=timeout) as response:
            return HttpResponse(response.status, response.read(max_bytes + 1), dict(response.headers), response.geturl())
    except HTTPError as error:
        try:
            return HttpResponse(error.code, error.read(max_bytes + 1), dict(error.headers or {}), url)
        finally:
            error.close()


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:\d{8}|\d{4}-\d{2}-\d{2})", value):
        raise RequestValidationError("Explicit dates must use YYYYMMDD or YYYY-MM-DD")
    try:
        parsed = datetime.strptime(value.replace("-", ""), "%Y%m%d").date()
    except ValueError:
        raise RequestValidationError("Invalid calendar date") from None
    return parsed


def _sequence(value, description):
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
        raise RequestValidationError("Explicit nonempty " + description + " required")
    return tuple(value)


def _json_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate JSON object key")
        value[key] = item
    return value


def _json_constant(_):
    raise ValueError("Nonfinite JSON constant")


def _known_provider_error(code, *, http_status, from_body, headers):
    """Map documented numeric codes, never provider-controlled message text.

    -403: invalid/missing token; -1403: missing privilege; 409: institution
    usage quota; 402: order credits exhausted. HTTP 403 by itself cannot
    distinguish credentials from entitlement, so its fixed message says so.
    """
    details = {"provider_code": code if from_body else None, "http_status": http_status}
    if code in (401, -403):
        return AuthenticationError("Provider API authentication failed; check the API token", **details)
    if code in (403, -1403):
        return EntitlementError("Provider denied access; verify API credentials and resource entitlement", **details)
    if code == 409:
        return QuotaError("Provider institution API usage quota is exhausted; check the institution limit", **details)
    if code == 402:
        return QuotaError("Provider order credits are exhausted; check the API order allowance", **details)
    if code == 429:
        retry = None
        if isinstance(headers, Mapping):
            for key, value in headers.items():
                if isinstance(key, str) and key.lower() == "retry-after" and isinstance(value, str) and re.fullmatch(r"\d{1,4}", value):
                    retry = min(int(value), 3600)
        return RateLimitError(retry, **details)
    return None


class DataYesClient:
    """One date per request, explicit pagination, no implicit retries or saving.

    transport(url, headers, timeout_seconds, max_response_bytes) -> HttpResponse
    may be injected for tests. Business requests are always to ENDPOINT.
    Request budgets apply to the entire lifetime of one client instance.
    """
    def __init__(self, token: str, *, limits: Limits | None = None,
                 transport: Callable = urllib_transport,
                 clock: Callable = time.monotonic, sleep: Callable = time.sleep):
        if not isinstance(token, str) or not token.strip():
            raise MissingTokenError("DATAYES_API_TOKEN is required; no request was made")
        token = token.strip()
        if any(ch.isspace() or ord(ch) < 33 or ord(ch) > 126 for ch in token):
            raise MissingTokenError("API token format is invalid; no request was made")
        self._token = token
        self.limits = limits if limits is not None else Limits()
        if not isinstance(self.limits, Limits):
            raise RequestValidationError("Limits must be an explicit Limits object")
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._next_request_at = None
        self.requests_made = 0

    def __repr__(self):
        return f"DataYesClient(requests_made={self.requests_made}, token=<redacted>)"

    @classmethod
    def from_environment(cls, *, environ: Mapping[str, str] | None = None, **kwargs):
        environment = os.environ if environ is None else environ
        return cls(environment.get(TOKEN_ENV, ""), **kwargs)

    def _get(self, query):
        if self.requests_made >= self.limits.max_requests:
            raise BudgetExceededError("Request budget reached; no additional request was made")
        now = self._clock()
        if self._next_request_at is not None and now < self._next_request_at:
            self._sleep(self._next_request_at - now)
        self._next_request_at = self._clock() + self.limits.min_interval_seconds
        self.requests_made += 1
        url = ENDPOINT + "?" + urlencode(query)
        failed = False
        try:
            response = self._transport(
                url, {"Authorization": "Bearer " + self._token, "Accept": "application/json"},
                self.limits.timeout_seconds, self.limits.max_response_bytes,
            )
        except Exception:
            failed = True
        if failed:
            # Raise outside the handler so the original exception, which could
            # contain a reflected Authorization header, is not retained.
            raise TransportError("Provider request failed; no automatic retry was made") from None
        if not isinstance(response, HttpResponse) or type(response.status) is not int:
            raise ResponseValidationError("Invalid transport response")
        if response.final_url != url or 300 <= response.status < 400:
            raise ResponseValidationError("Redirected provider responses are forbidden")
        valid_body_size = isinstance(response.body, bytes) and len(response.body) <= self.limits.max_response_bytes
        invalid_json = not valid_body_size
        payload = None
        if valid_body_size:
            try:
                payload = json.loads(response.body.decode("utf-8-sig"), parse_constant=_json_constant, object_pairs_hook=_json_object)
            except (UnicodeError, ValueError, TypeError, RecursionError, OverflowError):
                invalid_json = True
        if isinstance(payload, dict):
            # The business endpoint uses retCode; the official gateway can use
            # code. Known failures in either envelope never count as data success.
            for field in ("retCode", "code"):
                code = payload.get(field)
                if type(code) is int:
                    error = _known_provider_error(code, http_status=response.status,
                                                  from_body=True, headers=response.headers)
                    if error is not None:
                        raise error
        error = _known_provider_error(response.status, http_status=response.status,
                                      from_body=False, headers=response.headers)
        if error is not None:
            raise error
        if response.status != 200:
            raise TransportError("Provider returned an unsuccessful HTTP status", http_status=response.status)
        if not valid_body_size:
            raise ResponseValidationError("Provider response exceeds the byte limit or has invalid encoding")
        if invalid_json:
            raise ResponseValidationError("Provider response is not strict JSON") from None
        if not isinstance(payload, dict) or type(payload.get("retCode")) is not int or payload["retCode"] != 1:
            # retMsg is deliberately never copied: providers can reflect input.
            raise ResponseValidationError("Provider did not report successful retCode 1")
        if not isinstance(payload.get("data"), list):
            raise ResponseValidationError("Provider data must be a JSON array")
        return payload["data"]

    def _validate_row(self, row, day, tickers, fields):
        if not isinstance(row, dict) or any(field not in row for field in fields):
            raise ResponseValidationError("Provider row lacks a requested field")
        ticker = row.get("ticker")
        exchange = row.get("exchangeCD")
        if not isinstance(ticker, str) or not re.fullmatch(r"\d{6}", ticker):
            raise ResponseValidationError("Provider returned an invalid security code")
        if tickers is not None and ticker not in tickers:
            raise ResponseValidationError("Provider returned a security outside the explicit request")
        if not isinstance(exchange, str) or not re.fullmatch(r"[A-Z][A-Z0-9]{1,11}", exchange):
            raise ResponseValidationError("Provider returned an invalid exchange code")
        try:
            returned_day = _date(row.get("tradeDate"))
        except RequestValidationError:
            raise ResponseValidationError("Provider returned an invalid trading date") from None
        if returned_day != day:
            raise ResponseValidationError("Provider returned data outside the requested date")
        result = {}
        for field in fields:
            value = row[field]
            if field in NUMERIC_FIELDS and value is not None:
                try:
                    finite = type(value) in (float, int) and math.isfinite(value)
                except OverflowError:
                    finite = False
                if not finite:
                    raise ResponseValidationError("Provider returned an invalid numeric measurement")
                if field in PRICE_FIELDS and value <= 0:
                    raise ResponseValidationError("Provider returned a nonpositive price or adjustment factor")
                if field in NONNEGATIVE_FIELDS and value < 0:
                    raise ResponseValidationError("Provider returned a negative turnover measurement")
            if field == "isOpen" and value is not None and (type(value) is not int or value not in (0, 1)):
                raise ResponseValidationError("Provider returned an invalid trading-status value")
            if value is not None and type(value) not in (str, int, float, bool):
                raise ResponseValidationError("Provider returned a non-scalar field")
            if isinstance(value, float) and not math.isfinite(value):
                raise ResponseValidationError("Provider returned a nonfinite field")
            result[field] = value
        return result

    def fetch_daily(self, trade_dates: Sequence[str], tickers: Sequence[str] | None, *,
                    fields: Sequence[str] = DEFAULT_FIELDS, full_market: bool = False) -> FetchResult:
        dates = tuple(_date(v) for v in _sequence(trade_dates, "trading dates"))
        if len(set(dates)) != len(dates) or len(dates) > self.limits.max_dates:
            raise RequestValidationError("Duplicate dates or date-count budget exceeded")
        if (max(dates) - min(dates)).days + 1 > self.limits.max_date_span_days:
            raise RequestValidationError("Requested date span exceeds its budget")
        dates = tuple(sorted(dates))
        if type(full_market) is not bool:
            raise RequestValidationError("Full-market scope must be explicit")
        if full_market:
            if tickers is not None:
                raise RequestValidationError("Full-market requests require tickers=None")
            requested = None
        else:
            requested = _sequence(tickers, "security codes")
            if any(not isinstance(v, str) or not re.fullmatch(r"\d{6}", v) for v in requested):
                raise RequestValidationError("Security codes must be six-digit strings")
            if len(set(requested)) != len(requested) or len(requested) > self.limits.max_tickers:
                raise RequestValidationError("Duplicate security codes or ticker budget exceeded")
            requested = tuple(sorted(requested))
        chosen_fields = _sequence(fields, "fields")
        if any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", v) for v in chosen_fields):
            raise RequestValidationError("Invalid requested field name")
        if len(chosen_fields) > 100 or len(set(chosen_fields)) != len(chosen_fields) or not IDENTITY_FIELDS.issubset(chosen_fields):
            raise RequestValidationError("Requested fields must include unique identity fields within the field limit")
        if len(dates) > self.limits.max_requests - self.requests_made:
            raise BudgetExceededError("Remaining request budget cannot cover the explicit dates")
        all_rows = []
        coverage = []
        known_keys = set()
        requests_before = self.requests_made
        for day in dates:
            observed_tickers = set()
            page_fingerprints = set()
            page_counts = []
            date_start = len(all_rows)
            for offset in range(self.limits.max_pages_per_date):
                query = {
                    "tradeDate": day.strftime("%Y%m%d"), "field": ",".join(chosen_fields),
                    "pagenum": self.limits.first_page + offset, "pagesize": self.limits.page_size,
                }
                if requested is not None:
                    query["ticker"] = ",".join(requested)
                page = self._get(query)
                if len(page) > self.limits.page_size:
                    raise IncompleteDownloadError("Provider exceeded the requested page size")
                if len(all_rows) + len(page) > self.limits.max_rows:
                    raise BudgetExceededError("Row budget exceeded; partial rows were not returned as complete")
                cleaned = [self._validate_row(row, day, requested, chosen_fields) for row in page]
                fingerprint = hashlib.sha256(json.dumps(cleaned, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
                if cleaned and fingerprint in page_fingerprints:
                    raise IncompleteDownloadError("Repeated provider page; pagination may have been ignored")
                page_fingerprints.add(fingerprint)
                for row in cleaned:
                    key = (row["ticker"], row["exchangeCD"], day.isoformat())
                    if key in known_keys:
                        raise IncompleteDownloadError("Duplicate security/exchange/date key across provider rows")
                    known_keys.add(key)
                    observed_tickers.add(row["ticker"])
                all_rows.extend(cleaned)
                page_counts.append(len(cleaned))
                if len(page) < self.limits.page_size:
                    coverage.append({
                        "trade_date": day.isoformat(), "pages": len(page_counts),
                        "first_page": self.limits.first_page, "page_size": self.limits.page_size,
                        "pagination_convention_verified": False,
                        "page_rows": page_counts, "rows": len(all_rows) - date_start,
                        "full_market_requested": full_market,
                        "requested_tickers": list(requested) if requested is not None else None,
                        "observed_tickers": len(observed_tickers),
                        "missing_requested_tickers": sorted(set(requested) - observed_tickers) if requested is not None else None,
                        "terminal_page": "empty" if not page else "short",
                        "completeness_verified": False,
                    })
                    break
            else:
                raise BudgetExceededError("Page budget reached without a terminal page; result is incomplete")
        return FetchResult(tuple(all_rows), tuple(coverage), chosen_fields, self.requests_made - requests_before)
