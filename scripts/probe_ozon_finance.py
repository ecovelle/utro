#!/usr/bin/env python3
"""Read-only check: print public type metadata and response shape, never seller values."""
import datetime as dt
import json
import sys
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


if __name__ == '__main__':
    main()
