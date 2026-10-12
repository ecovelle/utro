#!/usr/bin/env python3
"""Read-only check: print public type metadata and response shape, never seller values."""
import datetime as dt
import json
import sys
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
import collect_cloud as collector
from ozon_finance import fetch_day, fetch_postings, fetch_types


def main():
    collector.OZ_ID = collector.env('OZON_CLIENT_ID')
    collector.OZ_KEY = collector.env('OZON_API_KEY')
    types = fetch_types(collector.oz_post)
    print('PUBLIC_ACCRUAL_TYPES=' + json.dumps(types, ensure_ascii=False), flush=True)
    day = (dt.datetime.now(ZoneInfo('Europe/Moscow')).date() - dt.timedelta(days=1)).isoformat()
    operations = fetch_day(collector.oz_post, day)
    # Only shapes/booleans; no amounts, posting numbers, product IDs or seller counts.
    print('BY_DAY_SCHEMA=' + json.dumps({'reachable': True, 'has_records': bool(operations),
          'fields': sorted(set().union(*(r.keys() for r in operations))) if operations else []}), flush=True)
    numbers = {row.get('unit_number') for row in operations if row.get('accrued_category') == 'POSTING' and row.get('unit_number')}
    postings = fetch_postings(collector.oz_post, sorted(numbers)[:200])
    shapes = {}
    for rows in postings.values():
        for row in rows:
            type_id = str(row.get('type_id'))
            shape = shapes.setdefault(type_id, {'fields': set(), 'quantity_null': False, 'quantity_positive': False, 'quantity_negative': False})
            shape['fields'].update(row)
            q = row.get('quantity')
            shape['quantity_null'] |= q is None
            shape['quantity_positive'] |= isinstance(q, int) and q > 0
            shape['quantity_negative'] |= isinstance(q, int) and q < 0
    for shape in shapes.values():
        shape['fields'] = sorted(shape['fields'])
    print('POSTINGS_SCHEMAS_BY_PUBLIC_TYPE=' + json.dumps(shapes, ensure_ascii=False), flush=True)
    sale_types = {int(i) for i, value in types.items() if value['name'] == 'SaleCommission'}
    relations = set()
    for operation in operations:
        for product in (operation.get('posting') or {}).get('products', []):
            commission = product.get('commission') or {}
            gross = commission.get('sale_amount') or {}
            if gross.get('amount') is None:
                continue
            candidates = [r for r in postings.get(operation.get('unit_number'), [])
                          if r.get('sku') == product.get('sku') and r.get('accrual_date') == day and r.get('type_id') in sale_types]
            matches = {}
            for field in ('commission', 'sale_commission', 'sale_amount'):
                value = commission.get(field) or {}
                matches[field] = any(value.get('amount') is not None and r.get('accrued', {}).get('amount') is not None
                    and value.get('currency') == r['accrued'].get('currency')
                    and Decimal(value['amount']) == Decimal(r['accrued']['amount']) for r in candidates)
            price = commission.get('seller_price') or {}
            value = Decimal(gross['amount'])
            relations.add(json.dumps({'realization_sign': '+' if value > 0 else '-' if value < 0 else '0',
               'has_commission_quantity': any(isinstance(r.get('quantity'), int) and r['quantity'] > 0 for r in candidates),
               'gross_equals_quantity_times_seller_price': any(price.get('amount') is not None
                   and price.get('currency') == gross.get('currency') and r.get('quantity') is not None
                   and abs(value) == abs(Decimal(price['amount']) * Decimal(r['quantity'])) for r in candidates),
               'matching_fields': matches}, sort_keys=True))
    print('SALE_FIELD_RELATIONS=' + json.dumps([json.loads(r) for r in sorted(relations)]), flush=True)


if __name__ == '__main__':
    main()
