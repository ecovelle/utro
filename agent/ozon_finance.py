"""Ozon's current accrual API. Raw responses remain in the runner's memory."""
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP


def amount(value):
    if not isinstance(value, dict) or value.get('amount') is None or not value.get('currency'):
        raise ValueError('Ozon: нет суммы или валюты начисления')
    number = Decimal(str(value['amount']))
    currency = str(value['currency']).upper()
    if not number.is_finite() or len(currency) != 3 or not currency.isalpha():
        raise ValueError('Ozon: неверная сумма или валюта начисления')
    return int((number * 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP)), currency


def normalize_day(operations, postings, types, sku_map, day):
    """Sum unique accruals once. Count units only after matching the two APIs."""
    from metrics import OZ_ART
    amounts, products = defaultdict(int), {}
    result = {'amounts': amounts, 'products': products, 'buyouts': 0, 'returns': 0, 'quantityError': None}
    sale_types = {int(i) for i, value in types.items() if value['name'] == 'SaleCommission'}
    quantity_error = None if sale_types else 'В справочнике отсутствует подтверждённый тип SaleCommission'
    pending = {}
    for number, rows in postings.items():
        pending[number] = [r for r in rows if r.get('accrual_date') == day and r.get('type_id') in sale_types]
    seen = set()
    for operation in operations:
        ident = operation.get('accrual_id')
        if not ident or operation.get('date') != day or ident in seen:
            raise ValueError('Ozon: неверное или повторное начисление при нормализации')
        seen.add(ident)
        value, currency = amount(operation.get('total_amount'))
        amounts[currency] += value
        for product in (operation.get('posting') or {}).get('products', []):
            platform_sku = str(product.get('sku'))
            article = sku_map.get(platform_sku)
            commission = product.get('commission')
            if not commission:
                continue  # Logistics alone is not a sale.
            gross = commission.get('sale_amount')
            if not gross or gross.get('amount') is None:
                quantity_error = 'В событии нет подтверждённой суммы реализации'
                continue
            gross_value, gross_currency = amount(gross)
            if not gross_value:
                if article is None or article in OZ_ART:
                    quantity_error = 'Нулевая реализация не подтверждает направление товарного события'
                continue
            if article is None:
                quantity_error = 'Не сопоставлен SKU из финансового события с артикулом'
                continue
            if article not in OZ_ART:
                continue
            try:
                fee = amount(commission.get('commission'))
                unit_price, unit_currency = amount(commission.get('seller_price'))
            except (ValueError, ArithmeticError):
                quantity_error = 'Нет подтверждённой цены или комиссии для количества'
                continue
            number = operation.get('unit_number')
            candidates = []
            for i, line in enumerate(pending.get(number, [])):
                q = line.get('quantity')
                if (str(line.get('sku')) == platform_sku and isinstance(q, int) and not isinstance(q, bool) and q > 0
                        and amount(line.get('accrued')) == fee and unit_currency == gross_currency
                        and abs(gross_value) == abs(unit_price) * q):
                    candidates.append((i, q))
            if not candidates or len({q for _, q in candidates}) != 1:
                quantity_error = 'Количество не подтверждено сопоставлением двух методов Ozon'
                continue
            i, q = candidates[0]
            pending[number].pop(i)  # One commission line cannot count twice.
            kind = 'buyouts' if gross_value > 0 else 'returns'
            result[kind] += q
            products.setdefault(article, {'buyouts': 0, 'returns': 0})[kind] += q
    for rows in pending.values():
        for line in rows:
            article = sku_map.get(str(line.get('sku')))
            if article is None or article in OZ_ART:
                if line.get('quantity'):
                    quantity_error = 'Есть несопоставленные товарные строки начислений'
    result['amounts'] = dict(amounts)
    if quantity_error:
        result.update(buyouts=None, returns=None, products={}, quantityError=quantity_error)
    return result


def fetch_types(post):
    response = post('/v1/finance/accrual/types', {})
    types = response.get('accrual_types') if isinstance(response, dict) else None
    if not isinstance(types, list) or not types:
        raise ValueError('Ozon: справочник начислений отсутствует')
    result = {}
    for row in types:
        if not isinstance(row, dict) or not isinstance(row.get('id'), int) or not row.get('name'):
            raise ValueError('Ozon: изменился формат справочника начислений')
        result[str(row['id'])] = {'name': row['name'], 'description': row.get('description') or ''}
    return result


def fetch_day(post, day):
    cursor, seen_cursors, output = '', set(), {}
    for _ in range(1000):
        response = post('/v1/finance/accrual/by-day', {'date': day, 'last_id': cursor})
        if not isinstance(response, dict) or not isinstance(response.get('accruals'), list) or not isinstance(response.get('last_id'), str):
            raise ValueError('Ozon: изменился формат дневных начислений')
        rows = response['accruals']
        for row in rows:
            if not isinstance(row, dict) or not row.get('accrual_id') or row.get('date') != day:
                raise ValueError('Ozon: нет идентификатора или неверная дата начисления')
            ident = str(row['accrual_id'])
            if ident in output and output[ident] != row:
                raise ValueError('Ozon: противоречащие записи начисления')
            output[ident] = row
        next_cursor = response['last_id']
        if not rows or not next_cursor:
            return list(output.values())
        if next_cursor == cursor or next_cursor in seen_cursors:
            raise ValueError('Ozon: пагинация начислений не продвигается')
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise ValueError('Ozon: превышен лимит страниц начислений')


def fetch_postings(post, numbers):
    numbers = sorted(set(numbers))
    result = {}
    for offset in range(0, len(numbers), 200):
        batch = numbers[offset:offset + 200]
        response = post('/v1/finance/accrual/postings', {'posting_numbers': batch})
        rows = response.get('posting_accruals') if isinstance(response, dict) else None
        if not isinstance(rows, list):
            raise ValueError('Ozon: изменился формат начислений по отправлениям')
        for row in rows:
            number = row.get('posting_number') if isinstance(row, dict) else None
            if number not in batch or not isinstance(row.get('accruals'), list) or number in result:
                raise ValueError('Ozon: неверное отправление в начислениях')
            result[number] = row['accruals']
        if set(batch) - result.keys():
            raise ValueError('Ozon: ответ начислений по отправлениям неполный')
    return result
