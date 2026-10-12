"""Pure calculations: event dates, order cohorts and return signs are distinct."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
import datetime as dt
from zoneinfo import ZoneInfo

WB_ART = ['Ecovelle245', 'Ecovelle290', 'ecovelle155', 'EcovelleMix']
OZ_ART = ['ecovelle_245', 'ecovelle_290', 'ecovelle_155']
NAMES = dict(zip(OZ_ART + WB_ART, ['Дневные', 'Ночные', 'Ежедневные',
                                 'Дневные', 'Ночные', 'Ежедневные', 'Микс (4 пачки)']))


def money(value):
    return int((Decimal(str(value or 0)) * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP))


def event_day(value, timezone='Europe/Moscow'):
    if not value:
        raise ValueError('API не вернул дату события')
    value = str(value)
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo:
        parsed = parsed.astimezone(ZoneInfo(timezone))
    return parsed.date().isoformat()


def covered(day, intervals):
    return any(start <= day <= end for start, end in intervals)


def normalize_oz(posting, scheme):
    items = []
    for record in posting.get('products', []):
        if record.get('offer_id') not in OZ_ART:
            continue
        price = record.get('price')
        currency = record.get('currency_code')
        if isinstance(price, dict):
            currency = price.get('currency')
            price = price.get('amount')
        if price is None:
            raise ValueError('Ozon: нет цены товара в отправлении')
        items.append({'sku': record['offer_id'], 'q': int(record['quantity']), 'price': money(price),
                      'currency': currency})
    if not items:
        return None
    ident = posting.get('posting_number')
    if not ident or not posting.get('status'):
        raise ValueError('Ozon: отправление без номера/статуса')
    day = event_day(posting.get('created_at') or posting.get('in_process_at'))
    analytics = posting.get('analytics_data') or {}
    cancellation = posting.get('cancellation') or {}
    return scheme + ':' + ident, {'date': day, 'scheme': scheme, 'status': posting['status'],
                                  'items': items, 'city': analytics.get('city') or 'Не указан',
                                  'country': analytics.get('country') or '',
                                  'warehouse': analytics.get('warehouse_name') or analytics.get('warehouse') or '',
                                  'cancelReason': str(cancellation.get('cancel_reason') or cancellation.get('cancel_reason_id') or posting.get('cancel_reason_id') or 'Не указана')}


def normalize_wb(record, kind):
    art = record.get('supplierArticle')
    if art not in WB_ART:
        return None
    ident = record.get('srid') if kind == 'orders' else record.get('saleID')
    if not ident:
        raise ValueError('WB: нет уникального идентификатора ' + kind)
    value = {'date': event_day(record.get('date')), 'sku': art, 'srid': record.get('srid'),
             'changed': record.get('lastChangeDate') or '', 'price': money(record.get('priceWithDisc'))}
    if kind == 'orders':
        cancelled = record.get('isCancel') is True
        value.update(cancelled=cancelled, cancelDate=event_day(record.get('cancelDate')) if cancelled and record.get('cancelDate') else None,
                     country=record.get('countryName') or 'Не указана', region=record.get('regionName') or 'Не указан')
    else:
        prefix = str(ident)[:1]
        if prefix not in ('S', 'R'):
            raise ValueError('WB: неизвестный тип saleID')
        value.update(kind='sale' if prefix == 'S' else 'return', payout=money(record.get('forPay')))
    return str(ident), value


def aggregate(state, market):
    daily = defaultdict(lambda: {'orders': 0, 'orderAmount': 0, 'active': 0, 'cancelledOrders': 0,
                                  'cancels': 0, 'buyouts': 0 if market == 'wb' else None,
                                  'returns': 0 if market == 'wb' else None,
                                  'netPayout': 0 if market == 'wb' else None, 'deliveredCohort': 0,
                                  'fbo': 0, 'fbs': 0, 'products': {}, 'geo': {}, 'reasons': {}})
    def product(day, sku):
        return daily[day]['products'].setdefault(sku, {'orders': 0, 'buyouts': 0 if market == 'wb' else None,
                                                      'returns': 0 if market == 'wb' else None})
    if market == 'oz':
        for record in state['oz'].values():
            row = daily[record['date']]
            for item in record['items']:
                q = item['q']
                row['orders'] += q
                row['orderAmount'] += q * item['price']
                row[record['scheme']] += q
                product(record['date'], item['sku'])['orders'] += q
                if record['status'] == 'cancelled':
                    row['cancelledOrders'] += q
                    reason = record['cancelReason']
                    row['reasons'][reason] = row['reasons'].get(reason, 0) + q
                else:
                    row['active'] += q
                    row['geo'][record['city']] = row['geo'].get(record['city'], 0) + q
                if record['status'] == 'delivered':
                    row['deliveredCohort'] += q
            # The list has status, not a confirmed handover date. Never invent event buyouts.
            row['cancels'] = None
    else:
        for record in state['wb_orders'].values():
            day = record['date']
            row = daily[day]
            row['orders'] += 1
            row['orderAmount'] += record['price']
            product(day, record['sku'])['orders'] += 1
            row['cancelledOrders'] += int(record['cancelled'])
            row['active'] += int(not record['cancelled'])
            if record['cancelled'] and record['cancelDate']:
                daily[record['cancelDate']]['cancels'] += 1
            if not record['cancelled']:
                row['geo'][record['country']] = row['geo'].get(record['country'], 0) + 1
        for record in state['wb_sales'].values():
            row = daily[record['date']]
            kind = 'buyouts' if record['kind'] == 'sale' else 'returns'
            row[kind] += 1
            product(record['date'], record['sku'])[kind] += 1
            row['netPayout'] += abs(record['payout']) * (1 if kind == 'buyouts' else -1)
    return dict(sorted(daily.items()))
