"""Ozon's current accrual API. Raw responses remain in the runner's memory."""


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
