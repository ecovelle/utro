import sys
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from ozon_finance import fetch_day, fetch_postings, fetch_types

DAY = '2026-10-11'


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
