import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from check_datayes_connection import connection_check
from datayes_client import HttpResponse


class ConnectionCheckTests(unittest.TestCase):
    def test_missing_token_makes_no_request_and_does_not_claim_connected(self):
        def forbidden(*args):
            self.fail('A missing token must not trigger transport')
        report = connection_check('2026-10-14', '000001', environ={}, transport=forbidden)
        self.assertEqual(report['status'], 'configuration_required')
        self.assertEqual(report['request_count'], 0)
        self.assertFalse(report['website_feed_connected'])

    def test_one_real_shaped_row_confirms_sample_only_and_publishes_no_measurements(self):
        row = {'ticker': '000001', 'exchangeCD': 'XSHE', 'tradeDate': '2026-10-14',
               'preClosePrice': 10, 'actPreClosePrice': 10, 'closePrice': 9.4,
               'turnoverVol': 123456, 'turnoverValue': 1180000, 'chgPct': -.06,
               'accumAdjFactor': 1, 'isOpen': 1}
        calls = []
        def transport(url, headers, timeout, maximum):
            calls.append(url)
            return HttpResponse(200, json.dumps({'retCode': 1, 'data': [row]}).encode(), {}, url)
        report = connection_check('2026-10-14', '000001', environ={'DATAYES_API_TOKEN': 'test-only-token'}, transport=transport)
        self.assertEqual(len(calls), 1)
        self.assertEqual(report['status'], 'connected_sample')
        self.assertTrue(report['source_return_consistent_with_reference'])
        self.assertFalse(report['full_year_coverage_verified'])
        self.assertFalse(report['website_feed_connected'])
        self.assertNotIn('closePrice', json.dumps(report))
        self.assertNotIn('test-only-token', json.dumps(report))

    def test_transport_exception_is_not_written_into_report(self):
        def transport(*args):
            raise RuntimeError('Authorization Bearer test-secret-reflection')
        report = connection_check('2026-10-14', '000001', environ={'DATAYES_API_TOKEN': 'test-secret-reflection'}, transport=transport)
        self.assertNotEqual(report['status'], 'connected_sample')
        self.assertEqual(report['request_count'], 1)
        self.assertNotIn('test-secret-reflection', json.dumps(report))

    def test_empty_result_cannot_confirm_connection(self):
        def transport(url, *args):
            return HttpResponse(200, b'{"retCode":1,"data":[]}', {}, url)
        report = connection_check('2026-10-14', '000001', environ={'DATAYES_API_TOKEN': 'test-only-token'}, transport=transport)
        self.assertEqual(report['status'], 'no_data')
        self.assertFalse(report['website_feed_connected'])

    def test_inconsistent_return_units_do_not_pass_the_measurement_check(self):
        row = {'ticker': '000001', 'exchangeCD': 'XSHE', 'tradeDate': '2026-10-14',
               'preClosePrice': 10, 'actPreClosePrice': 10, 'closePrice': 9.4,
               'turnoverVol': 123456, 'turnoverValue': 1180000, 'chgPct': -6,
               'accumAdjFactor': 1, 'isOpen': 1}
        def transport(url, *args):
            return HttpResponse(200, json.dumps({'retCode': 1, 'data': [row]}).encode(), {}, url)
        report = connection_check('2026-10-14', '000001', environ={'DATAYES_API_TOKEN': 'test-only-token'}, transport=transport)
        self.assertEqual(report['status'], 'return_reference_mismatch')
        self.assertEqual(report['validated_rows'], 0)
        self.assertFalse(report['source_return_consistent_with_reference'])
        self.assertFalse(report['website_feed_connected'])


if __name__ == '__main__':
    unittest.main()
