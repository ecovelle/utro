"""Sales Funnel orders; buyouts remain Statistics events, not order cohorts.
Official schema: https://dev.wildberries.ru/en/openapi/analytics
"""
import datetime as dt
from decimal import Decimal
from metrics import WB_NM, money

PATH = '/api/analytics/v3/sales-funnel/products'


def count(value):
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('WB: некорректное количество в воронке продаж')
    return value


def fetch_day(post, day):
    response = post(PATH, {'selectedPeriod': {'start': day, 'end': day},
                          'nmIds': list(WB_NM), 'skipDeletedNm': False,
                          'limit': 1000, 'offset': 0})
    data = response.get('data') if isinstance(response, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get('products'), list):
        raise ValueError('WB: нет списка товаров в воронке продаж')
    currency = data.get('currency')
    if not isinstance(currency, str) or len(currency) != 3:
        raise ValueError('WB: воронка не указала валюту')
    products, seen = {}, set()
    for item in data['products']:
        nm = item['product']['nmId']
        if nm not in WB_NM or nm in seen:
            raise ValueError('WB: неожиданный или повторный артикул в воронке')
        selected = item['statistic']['selected']
        if selected.get('period') != {'start': day, 'end': day}:
            raise ValueError('WB: воронка вернула другой период')
        orders, cancelled = count(selected.get('orderCount')), count(selected.get('cancelCount'))
        amount = selected.get('orderSum')
        if amount is None or not Decimal(str(amount)).is_finite() or Decimal(str(amount)) < 0 or cancelled > orders:
            raise ValueError('WB: некорректные итоги воронки')
        products[WB_NM[nm]] = {'orders': orders, 'cancelledOrders': cancelled, 'orderAmount': money(amount)}
        seen.add(nm)
    # Missing cards are not proof of zero orders. Keep the last successful day.
    if seen != set(WB_NM):
        raise ValueError('WB: воронка вернула не все выбранные товары')
    return {'products': products, 'currency': currency,
            **{key: sum(p[key] for p in products.values()) for key in ('orders', 'cancelledOrders', 'orderAmount')}}


def collect(state, post, today, now, budget=17):
    days = state.setdefault('wb_analytics', {}).setdefault('days', {})
    recent = [(today - dt.timedelta(days=n)).isoformat() for n in range(1, 8)]
    older = [(today - dt.timedelta(days=n)).isoformat() for n in range(8, 91)]
    # Missing first, then oldest refresh: historical cancellations keep updating.
    older.sort(key=lambda day: (day in days, days.get(day, {}).get('updatedAt', ''), day))
    for day in (recent + older)[:budget]:
        value = fetch_day(post, day)
        value['updatedAt'] = now.isoformat()
        days[day] = value
    state['updated']['wb_analytics'] = now.isoformat()


def apply_orders(days, analytics):
    for day, row in days.items():
        row['orderSource'] = 'statistics'
        row['orderCurrency'] = 'RUB'
        funnel = analytics.get(day)
        if funnel is None:
            continue
        row['statisticsOrders'] = row['orders']
        row['ordersDiffer'] = row['orders'] != funnel['orders']
        row['orderSource'] = 'analytics'
        row['orderUpdatedAt'] = funnel['updatedAt']
        row['orderCurrency'] = funnel['currency']
        for key in ('orders', 'orderAmount', 'cancelledOrders'):
            row[key] = funnel[key]
        row['active'] = row['orders'] - row['cancelledOrders']
        for sku, product in funnel['products'].items():
            row['products'].setdefault(sku, {'buyouts': 0, 'returns': 0})['orders'] = product['orders']
