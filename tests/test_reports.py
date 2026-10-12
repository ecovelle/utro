import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
import collect_cloud as collector
from build_brief import build_data, render
from history import add_coverage, empty_history, read_history, write_history
from metrics import aggregate, event_day, money, normalize_oz, normalize_wb
from cryptography.exceptions import InvalidTag


def state():
    result = empty_history()
    result['run'] = {'today': '2026-10-12', 'generated': '2026-10-12T05:00:00+03:00', 'status': {'oz': {'ok': True}, 'wb': {'ok': True}}}
    return result


def sale(ident='S1', date='2026-10-02', **extra):
    return {'saleID': ident, 'supplierArticle': 'Ecovelle245', 'date': date + 'T12:00:00',
            'lastChangeDate': '2026-10-03T10:00:00', 'srid': 'order1',
            'forPay': 124.33, 'priceWithDisc': 170.22, **extra}


def order(ident='order1', **extra):
    return {'srid': ident, 'supplierArticle': 'Ecovelle245', 'date': '2026-10-01T12:00:00',
            'lastChangeDate': '2026-10-03T10:00:00', 'isCancel': False,
            'priceWithDisc': 170.22, **extra}


def posting(**extra):
    return {'posting_number': 'p1', 'created_at': '2026-09-30T22:00:00Z', 'status': 'delivered',
            'products': [{'offer_id': 'ecovelle_245', 'quantity': 3, 'price': '3500.19', 'currency_code': 'KZT'}], **extra}


def put(s, bucket, normalized):
    ident, value = normalized
    s[bucket][ident] = value


class MetricsTests(unittest.TestCase):
    def test_returns_not_counted_as_sales(self):
        s = state()
        put(s, 'wb_sales', normalize_wb(sale(), 'sales'))
        put(s, 'wb_sales', normalize_wb(sale('R1', '2026-10-03'), 'sales'))
        rows = aggregate(s, 'wb')
        self.assertEqual(rows['2026-10-02']['buyouts'], 1)
        self.assertEqual(rows['2026-10-03']['buyouts'], 0)
        self.assertEqual(rows['2026-10-03']['returns'], 1)
        self.assertEqual(sum(r['netPayout'] for r in rows.values()), 0)

    def test_negative_return_not_double_negated(self):
        s = state()
        put(s, 'wb_sales', normalize_wb(sale('R1', forPay=-124.33), 'sales'))
        self.assertEqual(aggregate(s, 'wb')['2026-10-02']['netPayout'], -12433)

    def test_order_and_cancel_event_dates_are_distinct(self):
        s = state()
        put(s, 'wb_orders', normalize_wb(order(isCancel=True, cancelDate='2026-10-06T00:00:00'), 'orders'))
        rows = aggregate(s, 'wb')
        self.assertEqual(rows['2026-10-01']['orders'], 1)
        self.assertEqual(rows['2026-10-01']['cancelledOrders'], 1)
        self.assertEqual(rows['2026-10-01']['cancels'], 0)
        self.assertEqual(rows['2026-10-06']['cancels'], 1)

    def test_id_upsert_prevents_double_count_and_updates_cancel(self):
        s = state()
        put(s, 'wb_orders', normalize_wb(order(), 'orders'))
        put(s, 'wb_orders', normalize_wb(order(isCancel=True, cancelDate='2026-10-06'), 'orders'))
        row = aggregate(s, 'wb')['2026-10-01']
        self.assertEqual(row['orders'], 1)
        self.assertEqual(row['active'], 0)

    def test_missing_id_fails_instead_of_merging_unrelated_orders(self):
        with self.assertRaises(ValueError):
            normalize_wb(order(srid=None), 'orders')

    def test_unknown_sale_type_is_not_a_buyout(self):
        with self.assertRaises(ValueError):
            normalize_wb(sale('X1'), 'sales')

    def test_currency_and_fractional_money_are_preserved(self):
        self.assertEqual(money('0.105'), 11)
        self.assertEqual(money('124.33'), 12433)
        self.assertEqual(money('-124.33'), -12433)

    def test_ozon_utc_midnight_is_moscow_next_day(self):
        self.assertEqual(event_day('2026-09-30T22:00:00Z'), '2026-10-01')
        self.assertEqual(event_day('2026-10-01T00:05:00'), '2026-10-01')

    def test_ozon_status_is_cohort_not_delivery_date(self):
        s = state()
        put(s, 'oz', normalize_oz(posting(), 'fbo'))
        row = aggregate(s, 'oz')['2026-10-01']
        self.assertEqual(row['orders'], 3)
        self.assertEqual(row['deliveredCohort'], 3)
        self.assertIsNone(row['buyouts'])
        self.assertIsNone(row['netPayout'])

    def test_ozon_cancel_does_not_erase_order(self):
        s = state()
        put(s, 'oz', normalize_oz(posting(status='cancelled'), 'fbs'))
        row = aggregate(s, 'oz')['2026-10-01']
        self.assertEqual(row['orders'], 3)
        self.assertEqual(row['cancelledOrders'], 3)
        self.assertEqual(row['active'], 0)
        self.assertEqual(row['orderAmount'], 1050057)

    def test_unsupported_product_is_filtered(self):
        self.assertIsNone(normalize_wb(order(supplierArticle='another'), 'orders'))
        self.assertIsNone(normalize_oz(posting(products=[]), 'fbo'))


class HistoryTests(unittest.TestCase):
    def test_overlapping_intervals_merge_and_gaps_remain(self):
        self.assertEqual(add_coverage([['2026-01-01', '2026-01-03']], '2026-01-04', '2026-01-08'), [['2026-01-01', '2026-01-08']])
        self.assertEqual(len(add_coverage([['2026-01-01', '2026-01-03']], '2026-01-06', '2026-01-08')), 2)

    def test_encryption_roundtrip_wrong_password_and_tampering(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            s = state()
            s['private'] = 'seller private value'
            write_history(s, 'test-only', path)
            self.assertNotIn('seller private value', path.read_text())
            self.assertEqual(read_history('test-only', path), s)
            with self.assertRaises(InvalidTag):
                read_history('wrong', path)
            data = json.loads(path.read_text())
            data['ct'] = ('A' if data['ct'][0] != 'A' else 'B') + data['ct'][1:]
            path.write_text(json.dumps(data))
            with self.assertRaises(InvalidTag):
                read_history('test-only', path)

    def test_missing_history_initializes_but_bad_existing_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            self.assertEqual(read_history('test-only', path), empty_history())
            path.write_text('broken')
            with self.assertRaises(json.JSONDecodeError):
                read_history('test-only', path)


class BuildTests(unittest.TestCase):
    def test_uncovered_days_remain_absent_and_today_excluded(self):
        s = state()
        s['coverage']['wb'] = [['2026-10-01', '2026-10-03'], ['2026-10-06', '2026-10-11']]
        put(s, 'wb_orders', normalize_wb(order(date='2026-10-12T00:00:01'), 'orders'))
        days = build_data(s)['markets']['wb']['days']
        self.assertEqual(days['2026-10-02']['orders'], 0)
        self.assertNotIn('2026-10-04', days)
        self.assertNotIn('2026-10-12', days)

    def test_mixed_currency_does_not_sum_money(self):
        s = state()
        s['coverage']['oz'] = [['2026-10-01', '2026-10-11']]
        put(s, 'oz', normalize_oz(posting(), 'fbo'))
        other = posting(posting_number='p2')
        other['products'][0]['currency_code'] = 'RUB'
        put(s, 'oz', normalize_oz(other, 'fbo'))
        market = build_data(s)['markets']['oz']
        self.assertIsNone(market['currency'])
        self.assertIsNone(market['days']['2026-10-01']['orderAmount'])
        self.assertEqual(market['days']['2026-10-01']['orders'], 6)

    def test_json_script_cannot_be_broken_by_review_text(self):
        html = render({'review': '</script><script>alert(1)</script>'}, '<script>__DATA__</script>')
        self.assertEqual(html.count('</script>'), 1)
        self.assertIn('\\u003c', html)

    def test_stock_rate_excludes_cancelled_orders(self):
        s = state()
        put(s, 'wb_orders', normalize_wb(order(), 'orders'))
        put(s, 'wb_orders', normalize_wb(order('cancelled', isCancel=True), 'orders'))
        self.assertAlmostEqual(build_data(s)['stockRates']['wb']['Ecovelle245'], 1/30)


class CollectorTests(unittest.TestCase):
    def test_wb_paginates_short_pages_until_empty_and_deduplicates(self):
        first = order()
        second = order(isCancel=True, cancelDate='2026-10-04', lastChangeDate='2026-10-04T12:00:00')
        with patch.object(collector, 'wb_get', side_effect=[[first], [second], []]) as get:
            result = collector.wb_pages('orders', '2026-10-01')
        self.assertEqual(get.call_count, 3)
        self.assertEqual(len(result), 1)
        self.assertTrue(result['order1']['cancelled'])
        self.assertIn('2026-10-03T10%3A00%3A00', get.call_args_list[1].args[1])

    def test_wb_stuck_cursor_fails_explicitly(self):
        with patch.object(collector, 'wb_get', return_value=[order()]):
            with self.assertRaisesRegex(RuntimeError, 'курсор'):
                collector.wb_pages('orders', '2026-10-01')

    def test_wb_partial_fetch_does_not_mark_history_complete(self):
        s = state()
        with patch.object(collector, 'wb_pages', side_effect=[{'order1': {}}, RuntimeError('API failed')]):
            with self.assertRaises(RuntimeError):
                collector.wb_orders_sales(s)
        self.assertEqual(s['coverage']['wb'], [])
        self.assertEqual(s['wb_orders'], {})

    def test_wb_empty_feed_is_valid_zero_sales_period(self):
        s = state()
        with patch.object(collector, 'wb_pages', return_value={}), patch.object(collector, 'today', dt.date(2026,10,12)):
            collector.wb_orders_sales(s)
        self.assertEqual(s['coverage']['wb'], [['2026-07-15', '2026-10-11']])

    def test_wb_stock_accumulates_multiple_variants(self):
        rows = [{'vendorCode': 'Ecovelle245', 'warehouses': [{'warehouseName': 'Всего находится на складах', 'quantity': q}, {'warehouseName': 'Котовск', 'quantity': q}]} for q in (2, 3)]
        with patch.object(collector, 'wb_get', side_effect=[{'data': {'taskId':'t'}}, {'data': {'status':'done'}}, rows]), patch.object(collector.time, 'sleep'):
            result = collector.wb_stock()
        self.assertEqual(result['Ecovelle245']['total'], 5)
        self.assertEqual(result['Ecovelle245']['wh']['Котовск'], 5)

    def test_stock_task_timeout_does_not_download_unfinished_report(self):
        with patch.object(collector, 'wb_get', side_effect=[{'data': {'taskId':'t'}}] + [{'data': {'status':'processing'}}]*20) as get, patch.object(collector.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'не готов'):
                collector.wb_stock()
        self.assertEqual(get.call_count, 21)

    def test_ozon_changed_fbs_pagination_does_not_publish_partial_cohort(self):
        s = state()
        with patch.object(collector, 'today', dt.date(2026, 10, 12)), patch.dict(collector.os.environ, {'HISTORY_MONTHS':'1'}), patch.object(collector, 'oz_post', side_effect=[{'postings':[posting()], 'has_next':False, 'cursor':''}, {'result':{'postings':[]}}]):
            with self.assertRaisesRegex(RuntimeError, 'пагинации'):
                collector.oz_orders(s)
        self.assertEqual(s['oz'], {})
        self.assertEqual(s['coverage']['oz'], [])

    def test_ozon_cursor_continues_short_page_and_accepts_terminal_cursor(self):
        s = state()
        calls = []
        def api(path, body):
            calls.append((path, body))
            if path.endswith('/fbo/list') and body['cursor'] == '':
                return {'postings':[posting()], 'has_next':True, 'cursor':'next'}
            if path.endswith('/fbo/list'):
                return {'postings':[posting(posting_number='p2')], 'has_next':False, 'cursor':'terminal'}
            return {'postings':[], 'has_next':False, 'cursor':''}
        with patch.object(collector, 'today', dt.date(2026,10,12)), patch.dict(collector.os.environ, {'HISTORY_MONTHS':'1'}), patch.object(collector, 'oz_post', side_effect=api), patch.object(collector, 'log'):
            collector.oz_orders(s)
        self.assertEqual(len(s['oz']), 2)
        self.assertEqual(s['oz_api_version'], 2)
        self.assertEqual({p for p,_ in calls}, {'/v3/posting/fbo/list','/v4/posting/fbs/list'})
        self.assertTrue(any(b['cursor'] == 'next' for _,b in calls))
        self.assertTrue(all(b['limit'] == 100 and b['sort_dir'] == 'ASC' and 'offset' not in b for _,b in calls))

    def test_ozon_stuck_cursor_does_not_update_existing_history(self):
        s = state()
        s['oz'] = {'old':{'keep':True}}
        s['coverage']['oz'] = [['2026-10-01','2026-10-11']]
        replies = [{'postings':[posting()], 'has_next':True, 'cursor':'next'},
                   {'postings':[posting(posting_number='p2')], 'has_next':True, 'cursor':'next'}]
        with patch.object(collector, 'today', dt.date(2026,10,12)), patch.dict(collector.os.environ, {'HISTORY_MONTHS':'1'}), patch.object(collector, 'oz_post', side_effect=replies):
            with self.assertRaisesRegex(RuntimeError, 'не продвигается'):
                collector.oz_orders(s)
        self.assertEqual(s['oz'], {'old':{'keep':True}})
        self.assertNotIn('oz_api_version', s)
        self.assertEqual(s['coverage']['oz'], [['2026-10-01','2026-10-11']])

    def test_ozon_new_price_object_keeps_currency_and_cents(self):
        _, row = normalize_oz(posting(products=[{'offer_id':'ecovelle_245','quantity':2,'price':{'amount':'3500.19','currency':'KZT'}}]), 'fbo')
        self.assertEqual(row['items'][0]['price'], 350019)
        self.assertEqual(row['items'][0]['currency'], 'KZT')

    def test_both_core_sources_failing_preserves_history_and_page(self):
        from contextlib import ExitStack
        s = state()
        with ExitStack() as stack:
            stack.enter_context(patch.object(collector, 'env', return_value='test-only'))
            stack.enter_context(patch.object(collector, 'read_history', return_value=s))
            stack.enter_context(patch.object(collector, 'log'))
            stack.enter_context(patch.object(collector.sys, 'argv', ['collect_cloud.py']))
            writer = stack.enter_context(patch.object(collector, 'write_history'))
            for name in ['oz_orders','wb_orders_sales','oz_finance','oz_stock','oz_actions','wb_stock','wb_prices','wb_feedback','wb_promos']:
                stack.enter_context(patch.object(collector, name, side_effect=RuntimeError('source unavailable')))
            with self.assertRaisesRegex(RuntimeError, 'Оба источника'):
                collector.main()
            writer.assert_not_called()

    def test_one_core_failure_is_recorded_with_last_successful_data(self):
        from contextlib import ExitStack
        s = state()
        put(s, 'oz', normalize_oz(posting(), 'fbo'))
        with ExitStack() as stack:
            stack.enter_context(patch.object(collector, 'env', return_value='test-only'))
            stack.enter_context(patch.object(collector, 'read_history', return_value=s))
            stack.enter_context(patch.object(collector, 'log'))
            stack.enter_context(patch.object(collector.sys, 'argv', ['collect_cloud.py']))
            writer = stack.enter_context(patch.object(collector, 'write_history'))
            stack.enter_context(patch.object(collector, 'oz_orders', side_effect=RuntimeError('API failed')))
            stack.enter_context(patch.object(collector, 'wb_orders_sales', return_value=None))
            for name in ['oz_finance','oz_stock','oz_actions','wb_stock','wb_prices','wb_feedback','wb_promos']:
                stack.enter_context(patch.object(collector, name, return_value={}))
            collector.main()
            saved = writer.call_args.args[0]
            self.assertEqual(len(saved['oz']), 1)
            self.assertFalse(saved['run']['status']['oz']['ok'])
            self.assertTrue(saved['run']['status']['wb']['ok'])

    def test_date_month_shift_handles_leap_year(self):
        self.assertEqual(collector.month_shift(dt.date(2024,3,31),-1),dt.date(2024,2,29))
        self.assertEqual(collector.month_shift(dt.date(2026,3,31),-1),dt.date(2026,2,28))


if __name__ == '__main__':
    unittest.main()
