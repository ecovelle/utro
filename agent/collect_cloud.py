#!/usr/bin/env python3
"""Утро Ecovelle — сбор всех сырых данных Ozon + Wildberries НАПРЯМУЮ из облака."""
import json, os, sys, time, datetime as dt
import urllib.request, urllib.error, urllib.parse
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ERRORS = []
LAST_REQUEST = {}


def log(*a):
    print(*a, flush=True)


def env(key):
    v = os.environ.get(key)
    if v:
        return v.strip()
    for p in ('config/env.txt', '.env', 'config/.env'):
        f = os.path.join(HERE, p)
        if os.path.exists(f):
            for line in open(f, encoding='utf-8'):
                line = line.strip()
                if line.startswith(key + '='):
                    return line.split('=', 1)[1].strip()
    sys.exit(f'{key} не найден: задай переменную окружения или положи в config/env.txt')


def req(url, method='GET', headers=None, body=None, timeout=180, tries=4):
    """HTTP с ретраями: 429 — пауза 65 с, 5xx и сетевые сбои (в т.ч. DNS) — 5 с."""
    data = json.dumps(body, ensure_ascii=False).encode('utf-8') if body is not None else None
    last = None
    for i in range(tries):
        r = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            host = urllib.parse.urlsplit(url).netloc
            interval = 65 if host == 'statistics-api.wildberries.ru' else (1.2 if host == 'api-seller.ozon.ru' else 0.6)
            delay = interval - (time.monotonic() - LAST_REQUEST.get(host, 0))
            if delay > 0:
                time.sleep(delay)
            LAST_REQUEST[host] = time.monotonic()
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                raw = resp.read().decode('utf-8', 'replace')
            return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as e:
            txt = e.read().decode('utf-8', 'replace')[:300]
            last = f'{e.code} {txt}'
            if e.code == 429 and i < tries - 1:
                retry_after = e.headers.get('Retry-After', '65')
                try:
                    wait = max(1, float(retry_after))
                except ValueError:
                    from email.utils import parsedate_to_datetime
                    try:
                        wait = max(1, (parsedate_to_datetime(retry_after) - dt.datetime.now(dt.timezone.utc)).total_seconds())
                    except (TypeError, ValueError):
                        wait = 65
                if wait > 600:
                    raise RuntimeError('Лимит API: повтор разрешён позже; сохраняем последние успешные данные')
                if urllib.parse.urlsplit(url).path == '/v1/finance/accrual/postings':
                    # Retry-After is a minimum. Sustained overload needs a longer backoff.
                    if wait > 120:
                        raise RuntimeError('Лимит финансового API: длительное ожидание; сохраняем успешные партии')
                    wait = max(wait, min(120, 65 * (2 ** i)))
                log(f'  429 — жду {wait:g} с по Retry-After: {url[:80]}')
                time.sleep(wait)
                continue
            if e.code >= 500 and i < tries - 1:
                time.sleep(5)
                continue
            break
        except Exception as e:  # DNS/таймаут/обрыв
            last = str(e)[:200]
            if i < tries - 1:
                time.sleep(5)
                continue
    raise RuntimeError(f'{method} {url[:90]} -> {last}')


from history import read_history, write_history, add_coverage
from metrics import WB_ART, OZ_ART, normalize_oz, normalize_wb
from ozon_finance import amount as finance_amount, fetch_types, fetch_day, fetch_postings, normalize_day

WB_NM = {257464190: 'Ecovelle245', 269707700: 'Ecovelle290',
         264934861: 'ecovelle155', 271132665: 'EcovelleMix'}
WB_NORMAL = {'Ecovelle245': 504, 'Ecovelle290': 503, 'ecovelle155': 506, 'EcovelleMix': 606}
now = dt.datetime.now(ZoneInfo('Europe/Moscow'))
today = now.date()
WB_TOKEN = ''
OZ_ID = OZ_KEY = ''


def W(name, obj):
    # Ancillary snapshots are encrypted with the rest of the history; no raw files needed.
    log(f'  собран {name}')


def wb_get(host, path):
    return req(f'https://{host}{path}', headers={'Authorization': WB_TOKEN, 'Content-Type': 'application/json'})


def oz_post(path, body):
    return req('https://api-seller.ozon.ru' + path, 'POST',
               {'Client-Id': OZ_ID, 'Api-Key': OZ_KEY, 'Content-Type': 'application/json'}, body)


def month_shift(day, months):
    import calendar
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    return day.replace(year=year, month=month+1, day=min(day.day, calendar.monthrange(year, month+1)[1]))


def oz_orders(state):
    # Monthly windows avoid API range limits. Refresh recent statuses; keep older records.
    first = month_shift(today, -int(os.environ.get('HISTORY_MONTHS', '24')))
    start = first if not state['coverage']['oz'] else max(first, today - dt.timedelta(days=89))
    end = today + dt.timedelta(days=1)
    pending, sku_map = {}, {}
    left = start
    while left < end:
        right = min(month_shift(left.replace(day=1), 1), end)
        since = dt.datetime.combine(left, dt.time(), ZoneInfo('Europe/Moscow')).astimezone(dt.timezone.utc).isoformat()
        to = (dt.datetime.combine(right, dt.time(), ZoneInfo('Europe/Moscow')).astimezone(dt.timezone.utc) - dt.timedelta(milliseconds=1)).isoformat()
        for scheme, path in [('fbo', '/v2/posting/fbo/list'), ('fbs', '/v3/posting/fbs/list')]:
            offset = 0
            seen = set()
            while True:
                response = oz_post(path, {'dir': 'ASC', 'filter': {'since': since, 'to': to},
                                         'limit': 1000, 'offset': offset, 'with': {'analytics_data': True}})
                if not isinstance(response, dict) or 'result' not in response:
                    raise RuntimeError('Ozon: изменился формат ' + path)
                result = response['result']
                if scheme == 'fbs' and (not isinstance(result, dict) or not isinstance(result.get('has_next'), bool)):
                    raise RuntimeError('Ozon: нет признака завершения пагинации FBS')
                rows = result if scheme == 'fbo' else result.get('postings')
                if not isinstance(rows, list):
                    raise RuntimeError('Ozon: нет списка отправлений ' + path)
                page_ids = {r.get('posting_number') for r in rows}
                if rows and page_ids <= seen:
                    raise RuntimeError('Ozon: пагинация повторила страницу')
                seen |= page_ids
                for record in rows:
                    for product in record.get('products', []):
                        if product.get('sku') is not None and product.get('offer_id'):
                            sku_map[str(product['sku'])] = product['offer_id']
                    normalized = normalize_oz(record, scheme)
                    if normalized:
                        pending[normalized[0]] = normalized[1]
                more = len(rows) == 1000 if scheme == 'fbo' else result.get('has_next')
                if not more:
                    break
                if not rows or offset + 1000 >= 20000:
                    raise RuntimeError('Ozon: окно слишком большое для пагинации; данные не опубликованы как полные')
                offset += 1000
        left = right
    state['oz'].update(pending)
    state.setdefault('oz_sku_map', {}).update(sku_map)
    state['coverage']['oz'] = add_coverage(state['coverage']['oz'], start.isoformat(), (today-dt.timedelta(days=1)).isoformat())
    state['updated']['oz'] = now.isoformat()
    log(f'Ozon: {len(pending)} отправлений обновлено')


def oz_finance(state, status):
    finance = state.setdefault('oz_finance', {'days': {}, 'types': {}})
    days = finance['days']
    earliest = max(dt.date(2022, 1, 1), month_shift(today, -int(os.environ.get('HISTORY_MONTHS', '24'))))
    end = today - dt.timedelta(days=1)
    refresh = [end - dt.timedelta(days=n) for n in range(7) if end - dt.timedelta(days=n) >= earliest]
    budget = max(1, min(180, int(os.environ.get('OZON_FINANCE_BACKFILL_DAYS', '90'))))
    missing, cursor = [], end
    while cursor >= earliest and len(missing) < budget:
        if cursor.isoformat() not in days and cursor not in refresh:
            missing.append(cursor)
        cursor -= dt.timedelta(days=1)
    pending_quantity_days = [dt.date.fromisoformat(day) for day in sorted(days, reverse=True)
                             if days[day]['buyouts'] is None and earliest.isoformat() <= day <= end.isoformat()]
    requested = sorted(set(refresh + missing + pending_quantity_days[:30]), reverse=True)
    operations = {}
    try:
        finance['types'] = fetch_types(oz_post)
        for day in requested:
            key = day.isoformat()
            operations[key] = fetch_day(oz_post, key)
    except Exception:
        # Completed days are still safe to retain, even if an older day failed.
        if not operations:
            raise
        status['oz_finance'] = {'ok': False, 'error': 'Не все запрошенные дни начислений удалось обновить'}
    postings = finance.setdefault('postings', {})
    numbers, recent_numbers = [], set()
    sku_map = state.get('oz_sku_map', {})
    def quantity_product(product):
        if sku_map.get(str(product.get('sku'))) not in OZ_ART:
            return False
        try:
            return finance_amount((product.get('commission') or {}).get('sale_amount'))[0] != 0
        except (ValueError, ArithmeticError):
            return False
    for day, rows in operations.items():
        for row in rows:
            if row.get('accrued_category') != 'POSTING' or not row.get('unit_number'):
                continue
            # Fee-only postings and other products do not need quantity requests.
            eligible = any(quantity_product(p) for p in (row.get('posting') or {}).get('products', []))
            if not eligible:
                continue
            number = row['unit_number']
            if number not in numbers:
                numbers.append(number)
            if day >= (today - dt.timedelta(days=7)).isoformat():
                recent_numbers.add(number)
    legacy = {n for n in numbers if n in postings and any('seller_price' not in row for row in postings[n])}
    needed = ([n for n in numbers if n in recent_numbers and n not in postings] +
              [n for n in numbers if n in recent_numbers and n in postings] +
              [n for n in numbers if n not in recent_numbers and (n not in postings or n in legacy)])[:400]
    status['oz_quantities'] = {'ok': True}
    # The beta endpoint throttles large batches in this cabinet; request one posting.
    for offset in range(len(needed)):
        try:
            batch = fetch_postings(oz_post, needed[offset:offset + 1])
            sale_types = {int(i) for i, value in finance['types'].items() if value['name'] == 'SaleCommission'}
            for number, rows in batch.items():
                postings[number] = [{k: row.get(k) for k in ('accrual_date','sku','type_id','quantity','accrued','seller_price')}
                                    for row in rows if row.get('type_id') in sale_types]
        except Exception:
            status['oz_quantities'] = {'ok': False, 'error': 'Не все партии количеств обновились; успешные ответы сохранены'}
            break
    unresolved = False
    for day, rows in operations.items():
        normalized = normalize_day(rows, postings, finance['types'], state.get('oz_sku_map', {}), day)
        days[day] = normalized
        unresolved |= normalized['quantityError'] is not None
    if unresolved:
        status['oz_quantities'] = {'ok': False, 'error': 'Не все количества подтверждены: указаны пропуски по датам'}
    if operations:
        state['updated']['oz_finance'] = now.isoformat()
    status.setdefault('oz_finance', {'ok': True})
    log('Ozon: начисления сохранены в зашифрованную историю; полные даты и количества проверяются отдельно')


def wb_pages(kind, since):
    cursor = since + 'T00:00:00'
    output = {}
    for _ in range(200):
        rows = wb_get('statistics-api.wildberries.ru',
                      f'/api/v1/supplier/{kind}?dateFrom={urllib.parse.quote(cursor)}&flag=0')
        if not isinstance(rows, list):
            raise RuntimeError('WB: изменился формат ' + kind)
        if not rows:
            return output
        for record in rows:
            normalized = normalize_wb(record, kind)
            if normalized:
                ident, value = normalized
                if ident not in output or value['changed'] >= output[ident]['changed']:
                    output[ident] = value
        # WB explicitly requires advancing lastChangeDate until an empty page, regardless of page size.
        next_cursor = rows[-1].get('lastChangeDate')
        if not next_cursor or next_cursor <= cursor:
            raise RuntimeError('WB: курсор не продвигается; возможно, данные неполные')
        cursor = next_cursor
    raise RuntimeError('WB: слишком много страниц')


def wb_orders_sales(state):
    earliest = today - dt.timedelta(days=89)
    previous = state['updated'].get('wb')
    start = max(earliest, dt.date.fromisoformat(previous[:10]) - dt.timedelta(days=7)) if previous else earliest
    orders = wb_pages('orders', start.isoformat())
    sales = wb_pages('sales', start.isoformat())
    # Both feeds must complete before declaring coverage. Identity upserts prevent duplicates.
    state['wb_orders'].update(orders)
    state['wb_sales'].update(sales)
    state['coverage']['wb'] = add_coverage(state['coverage']['wb'], start.isoformat(), (today-dt.timedelta(days=1)).isoformat())
    state['updated']['wb'] = now.isoformat()
    log(f'WB: {len(orders)} заказов, {len(sales)} продаж/возвратов обновлено')


def oz_stock():
    output, offset = [], 0
    while True:
        response = oz_post('/v2/analytics/stock_on_warehouses', {'limit': 1000, 'offset': offset, 'warehouse_type': 'ALL'})
        rows = response.get('result', {}).get('rows')
        if not isinstance(rows, list):
            raise RuntimeError('Ozon: нет строк остатков')
        output.extend({'sku': r['item_code'], 'warehouse': r['warehouse_name'],
                       'free': r['free_to_sell_amount'], 'promised': r.get('promised_amount', 0),
                       'reserved': r.get('reserved_amount', 0)} for r in rows if r.get('item_code') in OZ_ART)
        if len(rows) < 1000:
            return output
        offset += 1000
        if offset >= 20000:
            raise RuntimeError('Ozon: остатки превышают лимит пагинации')


def oz_actions():
    response = req('https://api-seller.ozon.ru/v1/actions', headers={'Client-Id': OZ_ID, 'Api-Key': OZ_KEY})
    rows = response.get('result')
    if not isinstance(rows, list):
        raise RuntimeError('Ozon: нет списка акций')
    return rows


def wb_stock():
    t = wb_get('seller-analytics-api.wildberries.ru',
               '/api/v1/warehouse_remains?groupByNm=true&groupBySa=true') or {}
    task = (t.get('data') or {}).get('taskId')
    if not task:
        raise RuntimeError(f'нет taskId: {str(t)[:200]}')
    for _ in range(20):
        time.sleep(8)
        st = wb_get('seller-analytics-api.wildberries.ru',
                    f'/api/v1/warehouse_remains/tasks/{task}/status') or {}
        if ((st.get('data') or {}).get('status') or '') == 'done':
            break
    else:
        raise RuntimeError('WB: отчёт остатков не готов за отведённое время')
    d = wb_get('seller-analytics-api.wildberries.ru',
               f'/api/v1/warehouse_remains/tasks/{task}/download')
    if not isinstance(d, list):
        raise RuntimeError('WB: изменился формат отчёта остатков')
    out = {}
    for r in d:
        if r.get('vendorCode') not in WB_ART:
            continue
        g = lambda n: next((w.get('quantity', 0) for w in (r.get('warehouses') or [])
                            if w.get('warehouseName') == n), 0)
        wh = {w['warehouseName']: w['quantity'] for w in (r.get('warehouses') or [])
              if not w['warehouseName'].startswith('В пути') and w['warehouseName'] != 'Всего находится на складах'}
        existing = out.setdefault(r['vendorCode'], {'toClient': 0, 'returning': 0, 'total': 0, 'wh': {}})
        existing['toClient'] += g('В пути до получателей')
        existing['returning'] += g('В пути возвраты на склад WB')
        existing['total'] += g('Всего находится на складах')
        for name, count in wh.items():
            existing['wh'][name] = existing['wh'].get(name, 0) + count
    W('wb_stock.json', out)
    return out


def wb_prices():
    out = {}
    for nm, art in WB_NM.items():
        d = wb_get('discounts-prices-api.wildberries.ru',
                   f'/api/v2/list/goods/filter?limit=10&filterNmID={nm}') or {}
        goods = (d.get('data') or {}).get('listGoods')
        if not isinstance(goods, list):
            raise RuntimeError('WB: изменился формат цен')
        if not goods:
            continue
        g = goods[0]
        sz = (g.get('sizes') or [{}])[0]
        out[art] = {'nm': nm, 'price': sz.get('price'), 'disc': g.get('discount'),
                    'final': sz.get('discountedPrice'), 'normal': WB_NORMAL[art]}
        time.sleep(0.6)
    W('wb_prices.json', out)
    return out


THEMES = {
    'протекает': ['протек', 'течет', 'течёт', 'подтек'],
    'плохо впитывает': ['впитыв', 'намок', 'мокр'],
    'липучки/крепление': ['липуч', 'крепл', 'скатыва', 'сбива', 'съезжа', 'клеит'],
    'недовложение/комплект': ['недовлож', 'не хватает', 'не доложили', 'вместо', 'меньше чем', 'комплект'],
    'упаковка помята/вскрыта': ['упаковк', 'помят', 'порван', 'вскрыт', 'грязн'],
    'размер/длина': ['размер', 'коротк', 'маленьк', 'узк', 'длин'],
    'жёсткие/шуршат': ['жестк', 'жёстк', 'шурш', 'клеен', 'целлофан', 'раздраж', 'аллерг'],
    'запах': ['запах', 'пахн', 'вон'],
    'цена': ['дорог', 'цена', 'ценник'],
    'доставка': ['доставк', 'долго ш', 'срок'],
}


def wb_feedback(full=True):
    cu = wb_get('feedbacks-api.wildberries.ru', '/api/v1/feedbacks/count-unanswered') or {}
    qu = wb_get('feedbacks-api.wildberries.ru', '/api/v1/questions/count-unanswered') or {}
    cd, qd = cu.get('data') or {}, qu.get('data') or {}
    if 'countUnanswered' not in cd or 'countUnanswered' not in qd:
        raise RuntimeError('WB: изменился формат счётчиков отзывов/вопросов')
    out = {'unansweredTotal': cd.get('countUnanswered', 0),
           'unansweredToday': cd.get('countUnansweredToday', 0),
           'questionsUnanswered': qd.get('countUnanswered', 0)}
    fbs = []
    if full:
        for q in ('isAnswered=false&take=100&skip=0&order=dateDesc',
                  'isAnswered=true&take=200&skip=0&order=dateDesc'):
            r = wb_get('feedbacks-api.wildberries.ru', f'/api/v1/feedbacks?{q}') or {}
            rows = (r.get('data') or {}).get('feedbacks')
            if not isinstance(rows, list):
                raise RuntimeError('WB: изменился формат отзывов')
            fbs += rows
            time.sleep(0.6)
    cut = (today - dt.timedelta(days=30)).isoformat()
    dist, byArt, low, texts, n, ssum = {}, {}, [], [], 0, 0
    for f in fbs:
        d = (f.get('createdDate') or '')[:10]
        if d < cut:
            continue
        art = (f.get('productDetails') or {}).get('supplierArticle') or '?'
        if art not in WB_ART:
            continue
        v = int(f.get('productValuation') or 0)
        n += 1
        ssum += v
        dist[str(v)] = dist.get(str(v), 0) + 1
        byArt[art] = byArt.get(art, 0) + 1
        txt = ' '.join(x for x in (f.get('text'), f.get('pros'), f.get('cons')) if x).strip()
        if v <= 3:
            low.append({'d': d, 'art': art, 'v': v, 'text': (txt or '(без текста)')[:400]})
            texts.append(txt.lower())
    th = []
    for name, keys in THEMES.items():
        c = sum(1 for t in texts if any(k in t for k in keys))
        if c:
            th.append([name, c])
    th.sort(key=lambda x: -x[1])
    out['sample'] = True
    out['sampleLimit'] = 300
    out['periodStart'] = cut
    out['periodEnd'] = today.isoformat()
    out['sinceAug7'] = {'n': n, 'dist': dist, 'avg': (ssum / n) if n else 0, 'byArt': byArt}
    out['lowRecent'] = sorted(low, key=lambda r: r['d'], reverse=True)[:12]
    out['themes'] = th[:6]
    W('wb_feedback.json', out)
    return out


def wb_promos():
    a = today.isoformat() + 'T00:00:00Z'
    b = (today + dt.timedelta(days=45)).isoformat() + 'T00:00:00Z'
    output = []
    for offset in range(0, 10000, 50):
        response = wb_get('dp-calendar-api.wildberries.ru',
                          f'/api/v1/calendar/promotions?startDateTime={a}&endDateTime={b}'
                          f'&allPromo=true&limit=50&offset={offset}')
        rows = (response.get('data') or {}).get('promotions')
        if not isinstance(rows, list):
            raise RuntimeError('WB: изменился формат акций')
        output.extend({'id': p.get('id'), 'name': p.get('name'),
                       'start': (p.get('startDateTime') or '')[:10],
                       'end': (p.get('endDateTime') or '')[:10], 'type': p.get('type')} for p in rows)
        if len(rows) < 50:
            return output
    raise RuntimeError('WB: список акций превышает лимит пагинации')


def main():
    global today, now, WB_TOKEN, OZ_ID, OZ_KEY
    now = dt.datetime.now(ZoneInfo('Europe/Moscow'))
    today = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else now.date()
    password = env('PAGE_PASS')
    state = read_history(password)  # A wrong password must never erase accumulated history.
    OZ_ID, OZ_KEY, WB_TOKEN = env('OZON_CLIENT_ID'), env('OZON_API_KEY'), env('WB_TOKEN')
    status = {}
    core_success = 0
    for name, function in [('oz', oz_orders), ('wb', wb_orders_sales)]:
        try:
            function(state)
            status[name] = {'ok': True}
            core_success += 1
        except Exception as exc:
            status[name] = {'ok': False, 'error': str(exc)}
            log(f'!! {name}: {exc}')
    try:
        oz_finance(state, status)
    except Exception:
        status['oz_finance'] = {'ok': False, 'error': 'Начисления Ozon не обновились; сохранена предыдущая история'}
        status['oz_quantities'] = {'ok': False, 'error': 'Количества Ozon не обновились'}
        log('!! oz_finance: финансовый источник не обновился')
    for name, function in [('oz_stock', oz_stock), ('oz_actions', oz_actions), ('wb_stock', wb_stock),
                           ('wb_prices', wb_prices), ('wb_feedback', wb_feedback), ('wb_promos', wb_promos)]:
        try:
            data = function()
            state['snapshots'][name] = {'asOf': now.isoformat(), 'data': data}
            status[name] = {'ok': True}
        except Exception as exc:
            status[name] = {'ok': False, 'error': str(exc)}
            log(f'!! {name}: {exc}')
    if not core_success:
        raise RuntimeError('Оба источника заказов недоступны; сохраняем опубликованную страницу и историю')
    state['run'] = {'generated': now.isoformat(timespec='seconds'), 'today': today.isoformat(), 'status': status}
    write_history(state, password)
    failures = [name for name, value in status.items() if not value['ok']]
    if failures:
        log('::warning::Источники с ошибками: ' + ', '.join(failures))
    log('Зашифрованная история сохранена')


if __name__ == '__main__':
    main()
