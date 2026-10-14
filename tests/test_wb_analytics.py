import copy
import datetime as dt
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from metrics import WB_NM, normalize_wb
from wb_analytics import fetch_day, collect, apply_orders
from history import empty_history
from build_brief import build_data

DAY = '2026-10-13'


def response(day=DAY):
    return {'data': {'currency': 'KZT', 'products': [
        {'product': {'nmId': nm}, 'statistic': {'selected': {
            'period': {'start': day, 'end': day}, 'orderCount': n,
            'orderSum': n * 5000, 'cancelCount': 0, 'buyoutCount': 999}}}
        for nm, n in zip(WB_NM, [30, 20, 15, 10])]}}


class FunnelTests(unittest.TestCase):
    def test_complete_day_replaces_orders_not_event_sales(self):
        funnel = fetch_day(Mock(return_value=response()), DAY)
        funnel['updatedAt'] = '2026-10-14T08:00:00+03:00'
        days = {DAY: {'orders': 56, 'buyouts': 20, 'returns': 2, 'netPayout': 15000,
                      'products': {sku: {'orders': 14, 'buyouts': 5, 'returns': 0} for sku in WB_NM.values()}}}
        apply_orders(days, {DAY: funnel})
        row = days[DAY]
        self.assertEqual(row['orders'], 75)
        self.assertEqual(sum(p['orders'] for p in row['products'].values()), 75)
        self.assertEqual((row['buyouts'], row['returns'], row['netPayout']), (20, 2, 15000))
        self.assertEqual(row['statisticsOrders'], 56)
        self.assertTrue(row['ordersDiffer'])
        self.assertEqual(row['orderCurrency'], 'KZT')

    def test_missing_duplicate_or_wrong_period_not_zero(self):
        for mutate in (lambda r: r['data']['products'].pop(),
                       lambda r: r['data']['products'].append(r['data']['products'][0]),
                       lambda r: r['data']['products'][0]['statistic']['selected']['period'].update(end='2026-10-12'),
                       lambda r: r['data'].pop('currency'),
                       lambda r: r['data']['products'][0]['statistic']['selected'].pop('orderCount')):
            r = response(); mutate(r)
            with self.assertRaises(ValueError):
                fetch_day(Mock(return_value=r), DAY)

    def test_explicit_zero_is_valid(self):
        r = response()
        for p in r['data']['products']:
            p['statistic']['selected'].update(orderCount=0, orderSum=0)
        self.assertEqual(fetch_day(Mock(return_value=r), DAY)['orders'], 0)

    def test_partial_run_preserves_successful_days_and_previous_day(self):
        state = empty_history()
        state['wb_analytics'] = {'days': {'2026-10-12': {'orders': 90}}}
        with self.assertRaises(RuntimeError):
            collect(state, Mock(side_effect=[response(), RuntimeError('unavailable')]),
                    dt.date(2026,10,14), dt.datetime(2026,10,14))
        self.assertEqual(state['wb_analytics']['days'][DAY]['orders'], 75)
        self.assertEqual(state['wb_analytics']['days']['2026-10-12']['orders'], 90)

    def test_stats_only_marked_preliminary(self):
        days = {DAY: {'orders': 56}}
        apply_orders(days, {})
        self.assertEqual(days[DAY]['orderSource'], 'statistics')
        self.assertEqual(days[DAY]['orders'], 56)

    def test_stable_nm_id_survives_vendor_rename(self):
        r = {'srid': 'a', 'nmId': '257464190', 'supplierArticle': 'renamed', 'date': DAY}
        self.assertEqual(normalize_wb(r, 'orders')[1]['sku'], 'Ecovelle245')
        r.pop('nmId'); r['supplierArticle'] = ' ECOVELLE245 '
        self.assertEqual(normalize_wb(r, 'orders')[1]['sku'], 'Ecovelle245')

    def test_exported_payload_keeps_separate_order_and_payout_currencies(self):
        state = empty_history()
        state['run'] = {'today': '2026-10-14', 'generated': '2026-10-14', 'status': {}}
        state['coverage']['wb'] = [[DAY, DAY]]
        day = fetch_day(Mock(return_value=response()), DAY); day['updatedAt'] = '2026-10-14'
        state['wb_analytics'] = {'days': {DAY: day}}
        market = build_data(state)['markets']['wb']
        self.assertEqual(market['currency'], 'RUB')
        self.assertEqual(market['days'][DAY]['orderCurrency'], 'KZT')
        self.assertEqual(market['days'][DAY]['orders'], 75)

if __name__ == '__main__':
    unittest.main()
