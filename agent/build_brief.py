#!/usr/bin/env python3
"""Render the dashboard from encrypted history; no marketplace credentials required."""
import datetime as dt
import json
import os
import sys
from pathlib import Path
from history import read_history
from wb_analytics import apply_orders
from metrics import aggregate, covered, NAMES, OZ_ART, WB_ART

HERE = Path(__file__).resolve().parent


def build_data(state):
    run = state.get('run')
    if not run:
        raise ValueError('Нет завершённого сбора данных')
    today = dt.date.fromisoformat(run['today'])
    period_start = (today - dt.timedelta(days=90)).isoformat()
    period_end = (today - dt.timedelta(days=1)).isoformat()
    data = {'periodStart': period_start, 'generated': run['generated'], 'today': today.isoformat(), 'timezone': 'Europe/Moscow',
            'status': run['status'], 'names': NAMES, 'snapshots': state['snapshots'], 'markets': {},
            'stockRates': {'oz': {}, 'wb': {}}}
    # Publish only the last 90 full days; retain encrypted source history for recovery.
    for market in ('oz', 'wb'):
        intervals = [[max(a, period_start), min(b, period_end)] for a, b in state['coverage'][market]
                     if b >= period_start and a <= period_end]
        rows = aggregate(state, market)
        starts = [a for a, _ in intervals]
        start = min(starts) if starts else today.isoformat()
        days = {}
        cursor = dt.date.fromisoformat(start)
        while cursor < today:
            day = cursor.isoformat()
            if covered(day, intervals):
                defaults = {'orders': 0, 'orderAmount': 0, 'active': 0, 'cancelledOrders': 0,
                            'cancels': 0 if market == 'wb' else None,
                            'buyouts': 0 if market == 'wb' else None,
                            'returns': 0 if market == 'wb' else None,
                            'netPayout': 0 if market == 'wb' else None,
                            'deliveredCohort': 0, 'fbo': 0, 'fbs': 0, 'products': {}, 'geo': {}, 'reasons': {}}
                defaults.update(rows.get(day, {}))
                days[day] = defaults
            cursor += dt.timedelta(days=1)
        if market == 'wb':
            apply_orders(days, state.get('wb_analytics', {}).get('days', {}))
        codes = {item.get('currency') for record in state['oz'].values() if period_start <= record['date'] <= period_end for item in record['items'] if item.get('currency')} if market == 'oz' else {'RUB'}
        currency = next(iter(codes)) if len(codes) == 1 else (os.environ.get('OZON_CURRENCY', 'KZT') if not codes else None)
        if currency is None:
            for row in days.values():
                row['orderAmount'] = None  # Never add currencies without a confirmed conversion.
        data['markets'][market] = {'days': days, 'coverage': intervals, 'currency': currency,
                                   'currencyAssumed': market == 'oz' and not codes,
                                   'updatedAt': state['updated'].get(market),
                                   'products': OZ_ART if market == 'oz' else WB_ART,
                                   'buyoutsAvailable': market == 'wb'}
        if market == 'oz':
            finance = state.get('oz_finance', {}).get('days', {})
            finance = {day: row for day, row in finance.items() if period_start <= day <= period_end}
            for row in finance.values():
                for kind in ('buyouts', 'returns'):
                    if row[kind] is not None and (row[kind] < 0 or sum(p[kind] for p in row['products'].values()) != row[kind]):
                        raise ValueError('Ozon: итог количества не совпадает с товарными строками')
            currencies = {code for row in finance.values() for code in row['amounts']}
            finance_currency = next(iter(currencies)) if len(currencies) == 1 else (currency if not currencies else None)
            data['markets'][market].update(
                financeDays={day: {'netPayout': row['amounts'].get(finance_currency, 0) if finance_currency else None,
                                  'buyouts': row['buyouts'], 'returns': row['returns'], 'products': row['products'],
                                  'quantityError': row.get('quantityError')} for day, row in finance.items()},
                financeCurrency=finance_currency, financeUpdatedAt=state['updated'].get('oz_finance'),
                buyoutsAvailable=any(row['buyouts'] is not None for row in finance.values()))
        rate_start = (today - dt.timedelta(days=30)).isoformat()
        rate_end = (today - dt.timedelta(days=1)).isoformat()
        products = OZ_ART if market == 'oz' else WB_ART
        counts = dict.fromkeys(products, 0)
        records = state['oz'].values() if market == 'oz' else state['wb_orders'].values()
        for record in records:
            cancelled = record.get('cancelled') if market == 'wb' else record['status'] == 'cancelled'
            if rate_start <= record['date'] <= rate_end and not cancelled:
                if market == 'oz':
                    for item in record['items']:
                        counts[item['sku']] += item['q']
                else:
                    counts[record['sku']] += 1
        data['stockRates'][market] = {sku: count / 30 for sku, count in counts.items()}
    return data


def render(data, template=None):
    template = template if template is not None else (HERE / 'brief.html').read_text(encoding='utf-8')
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    return template.replace('__DATA__', payload)


def main():
    state = read_history(os.environ.get('PAGE_PASS', ''))
    data = build_data(state)
    recent = [(dt.date.fromisoformat(data['today']) - dt.timedelta(days=n)).isoformat() for n in range(1, 8)]
    finance = data['markets']['oz']['financeDays']
    health = {'finance_last_7_days_complete': all(day in finance for day in recent),
              'finance_currency_known': data['markets']['oz']['financeCurrency'] is not None,
              'quantities_last_7_days_confirmed': all(day in finance and finance[day]['buyouts'] is not None for day in recent),
              'quantity_gap_reasons': sorted({finance[day]['quantityError'] for day in recent if day in finance and finance[day]['quantityError']}),
              'wb_orders_last_7_days_analytics': all(data['markets']['wb']['days'].get(day, {}).get('orderSource') == 'analytics' for day in recent),
              'wb_orders_sources_agree_last_7_days': all(data['markets']['wb']['days'].get(day, {}).get('ordersDiffer') is False for day in recent),
              'sources': {name: bool(value.get('ok')) for name, value in data['status'].items()}}
    print('REPORT_HEALTH=' + json.dumps(health, ensure_ascii=False))
    (HERE / 'utro-ecovelle-plain.html').write_text(render(data), encoding='utf-8')
    print('Сводка собрана за полные дни до', data['today'], '(день не включён)')


if __name__ == '__main__':
    main()
