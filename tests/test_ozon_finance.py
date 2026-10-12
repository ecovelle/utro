import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from ozon_finance import amount, fetch_day, fetch_postings, fetch_types, normalize_day
from build_brief import build_data
from history import empty_history
import collect_cloud as collector
import datetime as dt
import io
import tempfile
from contextlib import redirect_stdout
import build_brief
import urllib.error
from email.message import Message
from unittest.mock import patch, MagicMock

DAY = '2026-10-11'
TYPES = {'69': {'name': 'SaleCommission'}}
SKU_MAP = {'123': 'ecovelle_245', '999': 'other-product'}


def event(gross='30.00', fee='-3.00', total='25.00', ident=1):
    m = lambda value: {'amount': value, 'currency': 'KZT'}
    return {'accrual_id': ident, 'date': DAY, 'unit_number': 'p1', 'accrued_category': 'POSTING',
            'total_amount': m(total), 'posting': {'products': [{'sku': 123, 'commission': {
                'sale_amount': m(gross), 'seller_price': m('10.00'), 'commission': m(fee)}}]}}


def line(quantity=3, fee='-3.00', **extra):
    return {'accrual_date': DAY, 'sku': 123, 'type_id': 69, 'quantity': quantity,
            'accrued': {'amount': fee, 'currency': 'KZT'}, **extra}


class FinanceCalculationTests(unittest.TestCase):
    def test_sale_units_not_logistics_units_and_no_double_financial_sum(self):
        result = normalize_day([event()], {'p1': [line(), line(type_id=32)]}, TYPES, SKU_MAP, DAY)
        self.assertEqual(result['buyouts'], 3)
        self.assertEqual(result['returns'], 0)
        self.assertEqual(result['amounts'], {'KZT': 2500})
        self.assertEqual(result['products']['ecovelle_245']['buyouts'], 3)

    def test_return_is_positive_count_and_negative_accrual(self):
        result = normalize_day([event(gross='-20',fee='2',total='-19')],
                               {'p1': [line(quantity=2,fee='2')]}, TYPES, SKU_MAP, DAY)
        self.assertEqual((result['buyouts'],result['returns']), (0,2))
        self.assertEqual(result['amounts']['KZT'], -1900)

    def test_signed_return_quantity_and_commission_notation(self):
        result=normalize_day([event(gross='-20',fee='-2',total='-19')],
                             {'p1':[line(quantity=-2,fee='2')]},TYPES,SKU_MAP,DAY)
        self.assertEqual((result['buyouts'],result['returns']),(0,2))

    def test_discount_compensation_and_price_list_commission(self):
        row=event(gross='27',fee='-1')
        commission=row['posting']['products'][0]['commission']
        commission['sale_commission']={'amount':'-3','currency':'KZT'}
        commission['bonus']={'amount':'3','currency':'KZT'}
        result=normalize_day([row],{'p1':[line()]},TYPES,SKU_MAP,DAY)
        self.assertEqual(result['buyouts'],3)
        commission['bonus']['currency']='RUB'
        self.assertIsNone(normalize_day([row],{'p1':[line()]},TYPES,SKU_MAP,DAY)['buyouts'])

    def test_price_can_come_from_matching_posting_line(self):
        for price in (None,{'amount':None,'currency':'KZT'}):
            row=event();row['posting']['products'][0]['commission']['seller_price']=price
            result=normalize_day([row],{'p1':[line(seller_price={'amount':'10','currency':'KZT'})]},TYPES,SKU_MAP,DAY)
            self.assertEqual(result['buyouts'],3)

    def test_negative_quantity_is_not_accepted_as_a_positive_sale(self):
        self.assertIsNone(normalize_day([event()],{'p1':[line(quantity=-3)]},TYPES,SKU_MAP,DAY)['buyouts'])

    def test_unconfirmed_units_do_not_erase_money_or_become_zero(self):
        result = normalize_day([event()], {'p1': [line(quantity=None)]}, TYPES, SKU_MAP, DAY)
        self.assertIsNone(result['buyouts'])
        self.assertIsNone(result['returns'])
        self.assertEqual(result['amounts']['KZT'], 2500)

    def test_quantity_must_agree_with_gross_and_unit_price(self):
        self.assertIsNone(normalize_day([event()], {'p1':[line(quantity=2)]}, TYPES, SKU_MAP, DAY)['buyouts'])

    def test_same_commission_line_cannot_count_two_events(self):
        result = normalize_day([event(),event(ident=2)], {'p1':[line()]}, TYPES, SKU_MAP, DAY)
        self.assertIsNone(result['buyouts'])

    def test_other_product_excluded_from_units_but_cabinet_accrual_retained(self):
        row = event(); row['posting']['products'][0]['sku'] = 999
        result = normalize_day([row], {'p1':[line(sku=999)]}, TYPES, SKU_MAP, DAY)
        self.assertEqual(result['buyouts'], 0)
        self.assertEqual(result['amounts']['KZT'], 2500)

    def test_unknown_sku_and_fee_only_are_not_fabricated_sales(self):
        self.assertIsNone(normalize_day([event()], {'p1':[line()]}, TYPES, {}, DAY)['buyouts'])
        row = event(); row['posting']['products'][0]['commission'] = None
        result = normalize_day([row], {'p1':[line(type_id=32)]}, TYPES, SKU_MAP, DAY)
        self.assertEqual(result['buyouts'], 0)

    def test_zero_realization_is_not_assumed_zero_units(self):
        result=normalize_day([event(gross='0',fee='0')],{},TYPES,SKU_MAP,DAY)
        self.assertIsNone(result['buyouts'])

    def test_money_validation_and_distinct_currencies(self):
        self.assertEqual(amount({'amount':'124.33','currency':'KZT'}), (12433,'KZT'))
        for value in [{'currency':'KZT'},{'amount':'NaN','currency':'KZT'}]:
            with self.assertRaises(ValueError):
                amount(value)
        row = event(ident=2); row['total_amount']={'amount':'5','currency':'RUB'}
        result = normalize_day([event(),row], {'p1':[line(),line()]}, TYPES, SKU_MAP, DAY)
        self.assertEqual(result['amounts'], {'KZT':2500,'RUB':500})

    def test_financial_days_independent_of_order_coverage(self):
        state = empty_history()
        state['run'] = {'today':'2026-10-12','generated':'2026-10-12T00:00:00+03:00','status':{}}
        state['oz_finance']={'days':{DAY:normalize_day([event()],{'p1':[line()]},TYPES,SKU_MAP,DAY)}}
        data = build_data(state)['markets']['oz']
        self.assertEqual(data['days'], {})
        self.assertEqual(data['financeDays'][DAY]['netPayout'], 2500)
        self.assertEqual(data['financeDays'][DAY]['buyouts'], 3)
        self.assertEqual(data['financeCurrency'], 'KZT')
        state['oz_finance']['days'][DAY]['amounts']['RUB']=100
        self.assertIsNone(build_data(state)['markets']['oz']['financeDays'][DAY]['netPayout'])

    def test_inconsistent_product_total_blocks_render(self):
        state=empty_history()
        state['run']={'today':'2026-10-12','generated':'2026-10-12T00:00:00+03:00','status':{}}
        row=normalize_day([event()],{'p1':[line()]},TYPES,SKU_MAP,DAY)
        row['buyouts']=4
        state['oz_finance']={'days':{DAY:row}}
        with self.assertRaisesRegex(ValueError,'не совпадает'):
            build_data(state)

    def test_report_health_logs_no_financial_values(self):
        state=empty_history()
        state['run']={'today':'2026-10-12','generated':'2026-10-12T00:00:00+03:00','status':{'oz_finance':{'ok':True}}}
        row=normalize_day([event(total='87871.23')],{'p1':[line()]},TYPES,SKU_MAP,DAY)
        state['oz_finance']={'days':{DAY:row}}
        output=io.StringIO()
        with tempfile.TemporaryDirectory() as root,patch.object(build_brief,'read_history',return_value=state),patch.object(build_brief,'HERE',Path(root)),redirect_stdout(output):
            (Path(root)/'brief.html').write_text('<html>__DATA__</html>')
            build_brief.main()
        self.assertIn('REPORT_HEALTH=',output.getvalue())
        self.assertNotIn('87871',output.getvalue())
        self.assertNotIn('p1',output.getvalue())

    def test_bounded_backfill_and_empty_days(self):
        state = empty_history(); status={}
        with patch.object(collector,'today',dt.date(2026,10,12)),patch.object(collector,'fetch_types',return_value=TYPES),patch.object(collector,'fetch_day',return_value=[]),patch.object(collector,'log'):
            collector.oz_finance(state,status)
        self.assertEqual(len(state['oz_finance']['days']),97)
        self.assertNotIn('2026-10-12',state['oz_finance']['days'])
        self.assertEqual(state['oz_finance']['days'][DAY]['buyouts'],0)

    def test_completed_day_survives_failure_of_an_older_day(self):
        state=empty_history();status={}
        with patch.object(collector,'today',dt.date(2026,10,12)),patch.object(collector,'fetch_types',return_value=TYPES),patch.object(collector,'fetch_day',side_effect=[[],RuntimeError('unavailable')]),patch.object(collector,'log'):
            collector.oz_finance(state,status)
        self.assertEqual(list(state['oz_finance']['days']),[DAY])
        self.assertFalse(status['oz_finance']['ok'])

    def test_successful_quantity_batches_survive_a_later_failure_and_resume(self):
        state=empty_history();state['oz_sku_map']=SKU_MAP;status={}
        operations=[]
        for n in range(40):
            row=event(ident=n+1);row['unit_number']='p'+str(n);operations.append(row)
        calls=[]
        def quantities(post,numbers):
            calls.append(list(numbers))
            if len(calls)==21:
                raise RuntimeError('rate limited')
            return {number:[line()] for number in numbers}
        with patch.object(collector,'today',dt.date(2026,10,12)),patch.object(collector,'fetch_types',return_value=TYPES),patch.object(collector,'fetch_day',side_effect=lambda post,day:operations if day==DAY else []),patch.object(collector,'fetch_postings',side_effect=quantities),patch.object(collector,'log'):
            collector.oz_finance(state,status)
            self.assertEqual(len(state['oz_finance']['postings']),20)
            self.assertEqual(state['oz_finance']['days'][DAY]['amounts']['KZT'],100000)
            self.assertIsNone(state['oz_finance']['days'][DAY]['buyouts'])
            collector.oz_finance(state,{})
        self.assertEqual(len(state['oz_finance']['postings']),40)
        self.assertEqual(state['oz_finance']['days'][DAY]['buyouts'],120)
        self.assertTrue(set(calls[21]).isdisjoint(set(n for call in calls[:20] for n in call)))

    def test_quantity_time_budget_retains_cash_and_successful_cache(self):
        state=empty_history();state['oz_sku_map']=SKU_MAP;status={}
        rows=[event(ident=1),event(ident=2)]
        rows[0]['unit_number']='p1';rows[1]['unit_number']='p2'
        with patch.object(collector,'today',dt.date(2026,10,12)),patch.object(collector,'fetch_types',return_value=TYPES),patch.object(collector,'fetch_day',side_effect=lambda post,day:rows if day==DAY else []),patch.object(collector,'fetch_postings',return_value={'p1':[line()]}) as fetch,patch.object(collector.time,'monotonic',side_effect=[0,0,601]),patch.object(collector,'log'):
            collector.oz_finance(state,status)
        self.assertEqual(fetch.call_count,1)
        self.assertEqual(set(state['oz_finance']['postings']),{'p1'})
        self.assertEqual(state['oz_finance']['days'][DAY]['amounts']['KZT'],5000)
        self.assertIsNone(state['oz_finance']['days'][DAY]['buyouts'])
        self.assertFalse(status['oz_quantities']['ok'])


class RateLimitTests(unittest.TestCase):
    def test_retry_after_is_honored(self):
        headers=Message();headers['Retry-After']='7'
        error=urllib.error.HTTPError('https://api-seller.ozon.ru/test',429,'rate limit',headers,io.BytesIO(b'limited'))
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{}'
        with patch.object(collector.urllib.request,'urlopen',side_effect=[error,response]),patch.object(collector.time,'sleep') as sleep,patch.object(collector,'log'),patch.dict(collector.LAST_REQUEST,{},clear=True):
            self.assertEqual(collector.req('https://api-seller.ozon.ru/test'),{})
            sleep.assert_any_call(7)

    def test_long_server_cooldown_does_not_hammer_api(self):
        headers=Message();headers['Retry-After']='3600'
        error=urllib.error.HTTPError('https://api-seller.ozon.ru/test',429,'rate limit',headers,io.BytesIO(b'limited'))
        with patch.object(collector.urllib.request,'urlopen',side_effect=error),patch.object(collector.time,'sleep') as sleep,patch.dict(collector.LAST_REQUEST,{},clear=True):
            with self.assertRaisesRegex(RuntimeError,'повтор разрешён позже'):
                collector.req('https://api-seller.ozon.ru/test')
            sleep.assert_not_called()


class FinanceApiTests(unittest.TestCase):
    def test_pagination_preserves_date_and_deduplicates_overlap(self):
        row = {'accrual_id': 1, 'date': DAY}
        call = Mock(side_effect=[{'accruals': [row], 'last_id': 'next'},
                                 {'accruals': [row, {'accrual_id': 2, 'date': DAY}], 'last_id': ''}])
        self.assertEqual(len(fetch_day(call, DAY)), 2)
        self.assertEqual([c.args[1] for c in call.call_args_list],
                         [{'date': DAY, 'last_id': ''}, {'date': DAY, 'last_id': 'next'}])

    def test_empty_day_is_valid(self):
        self.assertEqual(fetch_day(Mock(return_value={'accruals': [], 'last_id': ''}), DAY), [])

    def test_repeated_cursor_fails(self):
        row = {'accrual_id': 1, 'date': DAY}
        with self.assertRaisesRegex(ValueError, 'не продвигается'):
            fetch_day(Mock(return_value={'accruals': [row], 'last_id': 'stuck'}), DAY)

    def test_wrong_date_is_not_credited_to_requested_day(self):
        with self.assertRaisesRegex(ValueError, 'неверная дата'):
            fetch_day(Mock(return_value={'accruals': [{'accrual_id': 1, 'date': '2026-10-10'}], 'last_id': ''}), DAY)

    def test_missing_pagination_field_is_not_partial_success(self):
        with self.assertRaises(ValueError):
            fetch_day(Mock(return_value={'accruals': []}), DAY)

    def test_conflicting_duplicate_fails(self):
        with self.assertRaisesRegex(ValueError, 'противоречащие'):
            fetch_day(Mock(return_value={'accruals': [{'accrual_id': 1, 'date': DAY, 'value': 1},
                                                   {'accrual_id': 1, 'date': DAY, 'value': 2}], 'last_id': ''}), DAY)

    def test_postings_batches_and_preserves_multiple_equal_lines(self):
        def post(path, body):
            self.assertEqual(path, '/v1/finance/accrual/postings')
            self.assertLessEqual(len(body['posting_numbers']), 200)
            return {'posting_accruals': [{'posting_number': n, 'accruals': [{'quantity': 1}, {'quantity': 1}]}
                                        for n in body['posting_numbers']]}
        result = fetch_postings(post, [str(n) for n in range(401)])
        self.assertEqual(len(result), 401)
        self.assertEqual(len(result['0']), 2)

    def test_missing_requested_posting_is_not_zero(self):
        with self.assertRaisesRegex(ValueError, 'неполный'):
            fetch_postings(Mock(return_value={'posting_accruals': []}), ['requested'])

    def test_type_catalog_requires_ids_and_names(self):
        self.assertEqual(fetch_types(Mock(return_value={'accrual_types': [{'id': 3, 'name': 'sale'}]})),
                         {'3': {'name': 'sale', 'description': ''}})
        with self.assertRaises(ValueError):
            fetch_types(Mock(return_value={'accrual_types': [{'name': 'sale'}]}))
