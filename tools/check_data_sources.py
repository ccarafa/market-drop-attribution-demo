"""Run one bounded probe per connected data route and write a credential-free status.

Routes: AI-Ready gateway daily quotes (Bearer), 数据API商城 money flow (Bearer),
provider S3 object store (s3-data-auth) and the MCP gateway (X-API-KEY). Tokens
are read only from DATAYES_API_TOKEN and DATAYES_S3_TOKEN in the environment;
each route makes at most one request, nothing is retried, and the report keeps
only status categories, HTTP codes, row counts and a fingerprint of the sample
row. Missing tokens stop before any request for that route.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

GATEWAY_DAILY = "https://gw.datayes.com/aladdin_proxy/data_api/api/market/getMktEqud.json"
MALL_MONEYFLOW = "https://api.datayes.com/data/v1/api/market/getMktEquMfNew.json"
S3_SAMPLE = "https://bigdata-s3-data.datayes.com/pipeline/report/conversion/22d93390fccf8a66aecb22bb2b1478a1.html"
MCP_INIT = "https://dataapi-mcp-server.datayes.com/datayes-data/mcp"
MAX_BYTES = 256 * 1024


def default_transport(method, url, headers, body=None, timeout=30):
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, response.read(MAX_BYTES)
    except HTTPError as error:
        return error.code, error.read(MAX_BYTES)
    except (URLError, TimeoutError, OSError):
        return None, b""


def classify_rows(status, body):
    if status is None:
        return "transport_failed", None
    if status in (401, 403):
        return "entitlement_required", 0
    if status != 200:
        return "unexpected_response", 0
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "unexpected_response", 0
    code = payload.get("retCode", payload.get("code"))
    if code in (-403, -1403):
        return "authentication_failed" if code == -403 else "entitlement_required", 0
    rows = payload.get("data") if isinstance(payload.get("data"), list) else None
    if code == 1 and rows is not None:
        return ("connected_sample" if rows else "no_data"), len(rows)
    return "unexpected_response", 0


def fingerprint(body):
    try:
        rows = json.loads(body.decode("utf-8")).get("data") or []
        if rows:
            return hashlib.sha256(json.dumps(rows[0], sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    except Exception:
        pass
    return None


def probe_rest(name, route, url, token, trade_date, ticker, transport):
    report = {"name": name, "route": route, "auth": "Authorization: Bearer <DATAYES_API_TOKEN>", "status": "configuration_required", "request_count": 0, "rows": 0}
    if not token:
        return report
    query = urlencode({"ticker": ticker, "tradeDate": trade_date.replace("-", ""), "pagesize": 1, "pagenum": 1})
    status, body = transport("GET", url + "?" + query, {"Authorization": "Bearer " + token, "Accept": "application/json"})
    report["request_count"] = 1
    report["http_status"] = status
    report["status"], report["rows"] = classify_rows(status, body)
    if report["status"] == "connected_sample":
        report["response_row_sha256"] = fingerprint(body)
    return report


def probe_s3(token, transport):
    report = {"name": "S3 文件（公告与研报正文）", "route": "bigdata-s3-data.datayes.com", "auth": "s3-data-auth: <DATAYES_S3_TOKEN>", "status": "configuration_required", "request_count": 0}
    if not token:
        return report
    status, _ = transport("HEAD", S3_SAMPLE, {"s3-data-auth": token})
    report["request_count"] = 1
    report["http_status"] = status
    report["status"] = {200: "connected_sample", 403: "entitlement_required", 404: "no_data", None: "transport_failed"}.get(status, "unexpected_response")
    return report


def probe_mcp(token, transport):
    report = {"name": "MCP 网关（接口目录与搜索）", "route": "dataapi-mcp-server.datayes.com", "auth": "X-API-KEY: <DATAYES_S3_TOKEN>", "status": "configuration_required", "request_count": 0}
    if not token:
        return report
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "sources-check", "version": "1"}}}).encode("utf-8")
    status, body = transport("POST", MCP_INIT, {"Content-Type": "application/json", "Accept": "application/json, text/event-stream", "X-API-KEY": token}, payload)
    report["request_count"] = 1
    report["http_status"] = status
    if status == 200 and b"serverInfo" in body:
        report["status"] = "connected_sample"
    elif status in (401, 403):
        report["status"] = "entitlement_required"
    elif status is None:
        report["status"] = "transport_failed"
    else:
        report["status"] = "unexpected_response"
    return report


def check_sources(trade_date, ticker, *, environ=None, transport=None):
    environment = os.environ if environ is None else environ
    transport = transport or default_transport
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", trade_date) or not re.fullmatch(r"\d{6}", ticker):
        raise ValueError("Provide a YYYY-MM-DD date and a six-digit ticker")
    api_token = environment.get("DATAYES_API_TOKEN") or ""
    s3_token = environment.get("DATAYES_S3_TOKEN") or ""
    probes = [
        probe_rest("A股日行情（连接检查）", "AI-Ready 网关 gw.datayes.com", GATEWAY_DAILY, api_token, trade_date, ticker, transport),
        probe_rest("个股资金流向 getMktEquMfNew", "数据API商城 api.datayes.com/data/v1", MALL_MONEYFLOW, api_token, trade_date, ticker, transport),
        probe_s3(s3_token, transport),
        probe_mcp(s3_token, transport),
    ]
    configured = [p for p in probes if p["status"] != "configuration_required"]
    overall = "connected" if configured and all(p["status"] == "connected_sample" for p in configured) else ("configuration_required" if not configured else "attention")
    return {
        "schema_version": 1, "checked_at": datetime.now(timezone.utc).isoformat(),
        "request_scope": {"trade_date": trade_date, "ticker": ticker, "max_requests": 4},
        "secrets_present": {"DATAYES_API_TOKEN": bool(api_token), "DATAYES_S3_TOKEN": bool(s3_token)},
        "overall": overall, "probes": probes,
        "note": "Sanitized status only: no tokens, headers, provider messages or price rows are stored.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="One finished trading date, YYYY-MM-DD")
    parser.add_argument("--ticker", default="000001")
    parser.add_argument("--output", type=Path, default=Path(".runtime/data-sources-status.json"))
    args = parser.parse_args()
    try:
        report = check_sources(args.date, args.ticker)
    except ValueError as error:
        parser.error(str(error))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"overall": report["overall"], "probes": {p["name"]: p["status"] for p in report["probes"]}}, ensure_ascii=False))
    return 0 if report["overall"] == "connected" else 2


if __name__ == "__main__":
    raise SystemExit(main())
