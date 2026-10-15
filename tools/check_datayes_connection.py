"""Run one bounded official API check and write a credential-free status report.

This does not publish financial records, backfill history, or certify market
coverage. DATAYES_API_TOKEN is read only from the process environment.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from datayes_client import DataYesClient, DataYesError, Limits, MissingTokenError


def connection_check(trade_date, ticker, *, environ=None, transport=None):
    day = datetime.strptime(trade_date, '%Y-%m-%d').date()
    if not re.fullmatch(r'\d{6}', ticker):
        raise ValueError('Ticker must contain six digits')
    environment = os.environ if environ is None else environ
    report = {
        'schema_version': 1,
        'provider': 'DataYes',
        'api_name': 'Astock_market_daily',
        'checked_at': datetime.now(timezone.utc).isoformat(),
        'request_scope': {'trade_date': day.isoformat(), 'ticker': ticker},
        'limits': {'requests': 1, 'dates': 1, 'tickers': 1, 'page_size': 2, 'rows': 2},
        'status': 'configuration_required',
        'request_count': 0,
        'validated_rows': 0,
        'full_year_coverage_verified': False,
        'website_feed_connected': False,
        'financial_data_published': False,
    }
    client = None
    try:
        options = {'limits': Limits(max_dates=1, max_tickers=1, max_pages_per_date=1,
                                   max_requests=1, max_rows=2, page_size=2,
                                   timeout_seconds=30, min_interval_seconds=1)}
        if transport is not None:
            options['transport'] = transport
        client = DataYesClient.from_environment(environ=environment, **options)
        result = client.fetch_daily([day.isoformat()], [ticker])
        report['request_count'] = result.request_count
        if not result.rows:
            report['status'] = 'no_data'
        elif len(result.rows) != 1:
            report['status'] = 'unexpected_row_count'
        else:
            row = result.rows[0]
            numeric = ('closePrice', 'preClosePrice', 'chgPct', 'turnoverVol')
            if any(row.get(key) is None for key in numeric):
                report['status'] = 'required_measurements_missing'
            else:
                # Store only a fingerprint and checks, never the vendor's prices.
                digest = hashlib.sha256(json.dumps(row, sort_keys=True, separators=(',', ':'),
                                                  allow_nan=False).encode('utf-8')).hexdigest()
                return_difference = abs(row['closePrice'] / row['preClosePrice'] - 1 - row['chgPct'])
                consistent = return_difference <= 0.0001
                report.update(status='connected_sample' if consistent else 'return_reference_mismatch',
                              validated_rows=1 if consistent else 0,
                              response_row_sha256=digest,
                              source_return_consistent_with_reference=consistent)
                # The user still needs a verified data contract before activation.
                report['pagination_convention_verified'] = False
    except MissingTokenError:
        report['status'] = 'configuration_required'
    except DataYesError as error:
        report['status'] = getattr(error, 'error_category', 'provider_check_failed')
        report['request_count'] = client.requests_made if client is not None else 0
    except Exception:
        # Do not surface tracebacks or arbitrary errors that could echo a token.
        report['status'] = 'internal_check_failed'
        report['request_count'] = client.requests_made if client is not None else 0
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', required=True, help='One trading date, YYYY-MM-DD')
    parser.add_argument('--ticker', default='000001', help='One six-digit stock code')
    parser.add_argument('--output', type=Path, default=Path('.runtime/datayes-connection.json'))
    args = parser.parse_args()
    try:
        report = connection_check(args.date, args.ticker)
    except (ValueError, TypeError):
        parser.error('Provide a valid YYYY-MM-DD date and a six-digit ticker')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report['status'] == 'connected_sample' else 2


if __name__ == '__main__':
    raise SystemExit(main())
