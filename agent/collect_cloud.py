#!/usr/bin/env python3
"""Утро Ecovelle — сбор всех сырых данных Ozon + Wildberries НАПРЯМУЮ из облака.

Заменяет собой встроенный браузер, сниппеты collectors.md и merge_raw.py:
сеть до api-seller.ozon.ru и *.wildberries.ru открыта, поэтому каждый прогон
пересобирает данные с нуля за период «1-е число прошлого месяца … сегодня».
Никакого состояния между запусками не нужно — Mac не требуется.

Пишет в свою папку: oz_raw.json, wb_raw.json, wb_stock.json,
wb_feedback.json, wb_prices.json, wb_promos.json (форматы — как в докстринге
build_brief.py) и печатает короткую сводку + список источников, которые упали.

Ключи: OZON_CLIENT_ID, OZON_API_KEY, WB_TOKEN — из переменных окружения,
иначе из config/env.txt рядом со скриптом.

Запуск:  python3 collect_cloud.py [YYYY-MM-DD]
         (дата = «сегодня»; по умолчанию текущая дата в Asia/Shanghai)
Занимает ~2–4 минуты: между orders и sales у WB обязательная пауза 65 с.
"""
import json, os, sys, time, datetime as dt
import urllib.request, urllib.error, urllib.parse
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
ERRORS = []


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
            with urllib.request.urlopen(r, timeout=timeout) as resp:
                raw = resp.read().decode('utf-8', 'replace')
            return json.loads(raw) if raw.strip() else None
        except urllib.error.HTTPError as e:
            txt = e.read().decode('utf-8', 'replace')[:300]
            last = f'{e.code} {txt}'
            if e.code == 429 and i < tries - 1:
                log(f'  429 — жду 65 с и повторяю: {url[:80]}')
                time.sleep(65)
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


# ---------------------------------------------------------------- даты
now = dt.datetime.now(ZoneInfo('Asia/Shanghai'))
arg = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('-') else None
today = dt.date.fromisoformat(arg) if arg else now.date()
m1 = today.replace(day=1)                                    # 1-е текущего месяца
m0 = (m1 - dt.timedelta(days=1)).replace(day=1)              # 1-е прошлого месяца
since30 = (today - dt.timedelta(days=31)).isoformat()        # горизонт дневных рядов
TO = (today + dt.timedelta(days=1)).isoformat()
log(f'== Утро Ecovelle: сбор за {m0} … {today} (сегодня {today})')

OZ_ID, OZ_KEY, WB_TOKEN = env('OZON_CLIENT_ID'), env('OZON_API_KEY'), env('WB_TOKEN')

PADS = {'ecovelle_245', 'ecovelle_290', 'ecovelle_155'}          # offer_id Ozon
PADSKU = {5358370989, 5358332160, 5372746369}                    # sku Ozon (финансы)
WB_ART = ['Ecovelle245', 'Ecovelle290', 'ecovelle155', 'EcovelleMix']
WB_NM = {257464190: 'Ecovelle245', 269707700: 'Ecovelle290',
         264934861: 'ecovelle155', 271132665: 'EcovelleMix'}
WB_NORMAL = {'Ecovelle245': 504, 'Ecovelle290': 503, 'ecovelle155': 506, 'EcovelleMix': 606}
KZ_CITIES = ["Астана", "Нур-Султан", "Алматы", "Шымкент", "Караганда", "Актобе", "Тараз",
             "Павлодар", "Усть-Каменогорск", "Семей", "Атырау", "Костанай", "Кызылорда",
             "Уральск", "Петропавловск", "Актау", "Темиртау", "Туркестан", "Кокшетау",
             "Талдыкорган", "Экибастуз", "Рудный", "Жанаозен", "Балхаш", "Сатпаев",
             "Кентау", "Каскелен", "Степногорск", "Щучинск", "Риддер", "Жезказган",
             "Аксай", "Кульсары", "Байконур", "Талгар", "Есик", "Конаев", "Капшагай",
             "Сарань", "Шахтинск", "Житикара", "Аркалык", "Лисаковск", "Алтай", "Текели",
             "Хромтау", "Аягоз", "Каратау", "Шу", "Ленгер", "Сарыагаш", "Жетысай", "Арыс",
             "Абай", "Курчатов", "Атбасар", "Макинск", "Ерейментау", "Тайынша", "Булаево",
             "Шемонаиха", "Зайсан", "Аральск", "Казалинск", "Форт-Шевченко", "Эмба",
             "Шалкар", "Державинск", "Сергеевка", "Мамлютка", "Приозерск", "Косшы"]


def W(name, obj):
    json.dump(obj, open(os.path.join(HERE, name), 'w', encoding='utf-8'), ensure_ascii=False)
    log(f'  -> {name}')


# ================================================================ OZON
def ozon():
    H = {'Client-Id': OZ_ID, 'Api-Key': OZ_KEY, 'Content-Type': 'application/json'}
    post = lambda p, b: req('https://api-seller.ozon.ru' + p, 'POST', H, b)
    since, to = m0.isoformat() + 'T00:00:00Z', TO + 'T00:00:00Z'

    fbo, off = [], 0
    while True:
        r = post('/v2/posting/fbo/list', {'dir': 'ASC', 'filter': {'since': since, 'to': to},
                                          'limit': 1000, 'offset': off, 'with': {'analytics_data': True}})
        a = (r or {}).get('result') or []
        fbo += a
        if len(a) < 1000:
            break
        off += 1000
        time.sleep(0.7)
    time.sleep(0.7)
    fbs, off = [], 0
    while True:
        r = (post('/v3/posting/fbs/list', {'dir': 'ASC', 'filter': {'since': since, 'to': to},
                                           'limit': 1000, 'offset': off,
                                           'with': {'analytics_data': True}}) or {}).get('result') or {}
        fbs += r.get('postings') or []
        if not r.get('has_next'):
            break
        off += 1000
        time.sleep(0.7)
    log(f'  Ozon постинги: FBO {len(fbo)}, FBS {len(fbs)}')

    rows = []
    for p in fbo:
        its = [x for x in (p.get('products') or []) if x.get('offer_id') in PADS]
        if its:
            ad = p.get('analytics_data') or {}
            rows.append([(p.get('created_at') or '')[:10], 'fbo', p.get('status'),
                         [[x['offer_id'], x['quantity'], float(x['price'])] for x in its],
                         ad.get('city') or '', ad.get('warehouse_name') or '',
                         ad.get('delivery_type') or '', p.get('cancel_reason_id') or 0])
    for p in fbs:
        its = [x for x in (p.get('products') or []) if x.get('offer_id') in PADS]
        if its:
            ad = p.get('analytics_data') or {}
            rows.append([(p.get('in_process_at') or '')[:10], 'fbs', p.get('status'),
                         [[x['offer_id'], x['quantity'], float(x['price'])] for x in its],
                         ad.get('city') or '', ad.get('warehouse') or '',
                         ad.get('delivery_type') or '',
                         (p.get('cancellation') or {}).get('cancel_reason') or ''])

    daily, geo, cancels, month, whs, dtype, stat = {}, {}, {}, {}, {}, {}, {}
    transit = {'fbo': {'postings': 0, 'units': 0, 'sum': 0}, 'fbs': {'postings': 0, 'units': 0, 'sum': 0}}
    for d, sch, st, items, city, wh, dty, cr in rows:
        q = sum(i[1] for i in items)
        s = sum(i[1] * i[2] for i in items)
        m = d[:7]
        stat[st] = stat.get(st, 0) + 1
        M = month.setdefault(m, {'ordered': 0, 'rev': 0, 'canc': 0, 'cancRev': 0,
                                 'deliveredStatus': 0, 'transit': 0, 'fbo': 0, 'fbs': 0, 'postings': 0})
        M['postings'] += 1
        if st == 'cancelled':
            M['canc'] += q
            M['cancRev'] += s
            key = f'Отмена FBO (код Ozon {cr})' if sch == 'fbo' else (cr or 'без причины')
            cancels[key] = cancels.get(key, 0) + q
            continue
        M['ordered'] += q
        M['rev'] += s
        M[sch] += q
        if st == 'delivered':
            M['deliveredStatus'] += q
        else:
            M['transit'] += q
            transit[sch]['postings'] += 1
            transit[sch]['units'] += q
            transit[sch]['sum'] += s
        if d >= since30:
            for o, qq, pr in items:
                k = d + '|' + o
                row = daily.setdefault(k, [d, o, 0, 0, 0, 0, 0])
                row[2] += qq
                row[3] += qq * pr
                row[4 if sch == 'fbo' else 5] += qq
                if st == 'delivered':
                    row[6] += qq
        c = city or '(город не указан)'
        g = geo.setdefault(c, {'q': 0, 'fbo': 0, 'fbs': 0})
        g['q'] += q
        g[sch] += q
        if sch == 'fbo':
            whs[wh] = whs.get(wh, 0) + q
        dtype[dty or '?'] = dtype.get(dty or '?', 0) + q

    # финансы: прошлый и текущий месяц, границы считаются по датам (UTC-safe)
    ops = []
    for f in (m0, m1):
        end = (f.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
        page = 1
        while True:
            time.sleep(0.7)
            r = (post('/v3/finance/transaction/list',
                      {'filter': {'date': {'from': f.isoformat() + 'T00:00:00.000Z',
                                           'to': end.isoformat() + 'T23:59:59.999Z'},
                                  'operation_type': [], 'posting_number': '', 'transaction_type': 'all'},
                       'page': page, 'page_size': 1000}) or {}).get('result') or {}
            ops += r.get('operations') or []
            if page >= (r.get('page_count') or 1):
                break
            page += 1
    log(f'  Ozon финансовые операции: {len(ops)}')

    deliv, fin, other = {}, {}, {}
    for o in ops:
        d = (o.get('operation_date') or '')[:10]
        m = d[:7]
        if o.get('operation_type') == 'OperationAgentDeliveredToCustomer':
            items = [i for i in (o.get('items') or []) if i.get('sku') in PADSKU]
            if not items:
                continue
            x = deliv.setdefault(d, {'n': 0, 'acc': 0, 'amt': 0})
            x['n'] += len(items)
            x['acc'] += o.get('accruals_for_sale', 0)
            x['amt'] += o.get('amount', 0)
            F = fin.setdefault(m, {'acc': 0, 'comm': 0, 'log': 0, 'amt': 0, 'n': 0})
            F['n'] += len(items)
            F['acc'] += o.get('accruals_for_sale', 0)
            F['comm'] += o.get('sale_commission', 0)
            F['log'] += sum(s.get('price', 0) for s in (o.get('services') or []))
            F['amt'] += o.get('amount', 0)
        else:
            k = (o.get('operation_type_name') or '?') \
                .replace('Услуга за обработку операционных ошибок продавца: ', 'Штраф: ') \
                .replace('Обработка операционных ошибок продавца: ', 'Штраф: ')[:60]
            t = other.setdefault(m, {}).setdefault(k, {'n': 0, 'amt': 0})
            t['n'] += 1
            t['amt'] += o.get('amount', 0)

    time.sleep(0.7)
    stock = (post('/v2/analytics/stock_on_warehouses',
                  {'limit': 100, 'offset': 0, 'warehouse_type': 'ALL'}) or {}).get('result') or {}
    time.sleep(0.7)
    act = req('https://api-seller.ozon.ru/v1/actions', 'GET',
              {'Client-Id': OZ_ID, 'Api-Key': OZ_KEY}) or {}

    W('oz_raw.json', {
        'since': since30,
        'daily': sorted(daily.values()),
        'deliv': {d: v for d, v in deliv.items() if d >= since30},
        'fin': fin, 'other': other, 'month': month, 'transit': transit, 'cancels': cancels,
        'geo': sorted(geo.items(), key=lambda kv: -kv[1]['q'])[:40],
        'kzCities': KZ_CITIES, 'whs': whs, 'dtype': dtype, 'stat': stat,
        'stock': [{'o': r['item_code'], 'wh': r['warehouse_name'], 'free': r['free_to_sell_amount'],
                   'promised': r['promised_amount'], 'reserved': r['reserved_amount']}
                  for r in (stock.get('rows') or []) if r.get('item_code') in PADS],
        'actions': [{'id': a['id'], 'title': (a.get('title') or '').strip(),
                     'from': a['date_start'][:10], 'to': a['date_end'][:10],
                     'part': a.get('is_participating'), 'pot': a.get('potential_products_count')}
                    for a in (act.get('result') or [])],
    })
    return month


# ================================================================ WB
def wb_get(host, path):
    return req(f'https://{host}{path}', 'GET', {'Authorization': WB_TOKEN, 'Content-Type': 'application/json'})


def wb_stats():
    """orders + sales за период; между ними обязательная пауза 65 с (лимит 1 запрос/мин)."""
    since = m0.isoformat()
    O = wb_get('statistics-api.wildberries.ru',
               f'/api/v1/supplier/orders?dateFrom={since}T00:00:00&flag=0') or []
    log(f'  WB orders: {len(O)} (пауза 65 с перед sales)')
    time.sleep(65)
    S = wb_get('statistics-api.wildberries.ru',
               f'/api/v1/supplier/sales?dateFrom={since}T00:00:00&flag=0') or []
    log(f'  WB sales: {len(S)}')

    PA = set(WB_ART)
    daily, sales, month, countries, regions, last = {}, {}, {}, {}, {}, {}
    for r in O:
        if r.get('supplierArticle') not in PA:
            continue
        d = (r.get('date') or '')[:10]
        if d < since:
            continue
        a = r['supplierArticle']
        k = d + '|' + a
        row = daily.setdefault(k, [d, a, 0, 0, 0])
        M = month.setdefault(d[:7], {'ordered': 0, 'rev': 0, 'canc': 0, 'cancRev': 0, 'buy': 0, 'pay': 0})
        if r.get('isCancel'):
            row[4] += 1
            M['canc'] += 1
            M['cancRev'] += round(r.get('priceWithDisc') or 0)
        else:
            row[2] += 1
            row[3] += round(r.get('priceWithDisc') or 0)
            M['ordered'] += 1
            M['rev'] += round(r.get('priceWithDisc') or 0)
            countries[r.get('countryName') or '?'] = countries.get(r.get('countryName') or '?', 0) + 1
            reg = r.get('regionName') or r.get('oblastOkrugName') or '?'
            regions[reg] = regions.get(reg, 0) + 1
            if a not in last or d > last[a]:
                last[a] = d
    sold_srid = set()
    for r in S:
        if r.get('supplierArticle') not in PA:
            continue
        d = (r.get('date') or '')[:10]
        if d < since:
            continue
        a = r['supplierArticle']
        sold_srid.add(r.get('srid'))
        k = d + '|' + a
        row = sales.setdefault(k, [d, a, 0, 0, 0])
        row[2] += 1
        row[3] += round(r.get('forPay') or 0)
        row[4] += round(r.get('priceWithDisc') or 0)
        M = month.setdefault(d[:7], {'ordered': 0, 'rev': 0, 'canc': 0, 'cancRev': 0, 'buy': 0, 'pay': 0})
        M['buy'] += 1
        M['pay'] += round(r.get('forPay') or 0)
    # «в пути»: заказ не отменён и его srid ещё не встречался в выкупах
    tr = {'n': 0, 'sum': 0, 'byArt': {}}
    for r in O:
        if r.get('supplierArticle') not in PA or r.get('isCancel'):
            continue
        if (r.get('date') or '')[:10] < since or r.get('srid') in sold_srid:
            continue
        tr['n'] += 1
        tr['sum'] += round(r.get('priceWithDisc') or 0)
        a = r['supplierArticle']
        tr['byArt'][a] = tr['byArt'].get(a, 0) + 1

    W('wb_raw.json', {
        'since': since,
        'daily': sorted(daily.values()), 'sales': sorted(sales.values()),
        'month': month, 'transit': tr, 'lastOrder': last,
        'countries': sorted(countries.items(), key=lambda kv: -kv[1]),
        'regions': sorted(regions.items(), key=lambda kv: -kv[1])[:20],
    })
    return month


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
    d = wb_get('seller-analytics-api.wildberries.ru',
               f'/api/v1/warehouse_remains/tasks/{task}/download') or []
    out = {}
    for r in d:
        if r.get('vendorCode') not in WB_ART:
            continue
        g = lambda n: next((w.get('quantity', 0) for w in (r.get('warehouses') or [])
                            if w.get('warehouseName') == n), 0)
        wh = {w['warehouseName']: w['quantity'] for w in (r.get('warehouses') or [])
              if not w['warehouseName'].startswith('В пути') and w['warehouseName'] != 'Всего находится на складах'}
        out[r['vendorCode']] = {'toClient': g('В пути до получателей'),
                                'returning': g('В пути возвраты на склад WB'),
                                'total': g('Всего находится на складах'), 'wh': wh}
    W('wb_stock.json', out)
    return out


def wb_prices():
    out = {}
    for nm, art in WB_NM.items():
        d = wb_get('discounts-prices-api.wildberries.ru',
                   f'/api/v2/list/goods/filter?limit=10&filterNmID={nm}') or {}
        goods = (d.get('data') or {}).get('listGoods') or []
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
    out = {'unansweredTotal': cd.get('countUnanswered', 0),
           'unansweredToday': cd.get('countUnansweredToday', 0),
           'questionsUnanswered': qd.get('countUnanswered', 0)}
    fbs = []
    if full:
        for q in ('isAnswered=false&take=100&skip=0&order=dateDesc',
                  'isAnswered=true&take=200&skip=0&order=dateDesc'):
            r = wb_get('feedbacks-api.wildberries.ru', f'/api/v1/feedbacks?{q}') or {}
            fbs += (r.get('data') or {}).get('feedbacks') or []
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
    out['sinceAug7'] = {'n': n, 'dist': dist, 'avg': (ssum / n) if n else 0, 'byArt': byArt}
    out['lowRecent'] = sorted(low, key=lambda r: r['d'], reverse=True)[:12]
    out['themes'] = th[:6]      # черновик — агент переписывает по текстам lowRecent
    W('wb_feedback.json', out)
    return out


def wb_promos():
    a = today.isoformat() + 'T00:00:00Z'
    b = (today + dt.timedelta(days=45)).isoformat() + 'T00:00:00Z'
    d = wb_get('dp-calendar-api.wildberries.ru',
               f'/api/v1/calendar/promotions?startDateTime={a}&endDateTime={b}'
               f'&allPromo=true&limit=50&offset=0') or {}
    pr = [{'id': p.get('id'), 'name': p.get('name'), 'start': (p.get('startDateTime') or '')[:10],
           'end': (p.get('endDateTime') or '')[:10], 'type': p.get('type')}
          for p in ((d.get('data') or {}).get('promotions') or [])]
    W('wb_promos.json', pr)
    return pr


# ================================================================ run
def safe(name, fn, *a):
    try:
        log(f'-- {name}')
        return fn(*a)
    except Exception as e:
        ERRORS.append(f'{name}: {e}')
        log(f'!! {name} упал: {e}')
        return None


ozm = safe('Ozon', ozon)
wbm = safe('WB orders/sales', wb_stats)
ws = safe('WB остатки', wb_stock)
safe('WB цены', wb_prices)
safe('WB отзывы', wb_feedback)
safe('WB акции', wb_promos)

cm, pm = today.strftime('%Y-%m'), m0.strftime('%Y-%m')
log('\n== ИТОГ')
if ozm:
    log(f'  Ozon {cm}: заказано {ozm.get(cm, {}).get("ordered", 0)} шт / '
        f'{ozm.get(cm, {}).get("rev", 0):.0f} ₸, отмен {ozm.get(cm, {}).get("canc", 0)}')
if wbm:
    log(f'  WB   {cm}: заказано {wbm.get(cm, {}).get("ordered", 0)} шт, '
        f'выкуплено {wbm.get(cm, {}).get("buy", 0)}, отмен {wbm.get(cm, {}).get("canc", 0)}')
if ws:
    log(f'  WB склады: свободно {sum(v["total"] for v in ws.values())}, '
        f'возвраты в пути {sum(v["returning"] for v in ws.values())}')
if ERRORS:
    log('  НЕ СОБРАНО: ' + '; '.join(ERRORS))
    log('  (сводку собрать всё равно, отметив недостающий источник)')
else:
    log('  все источники собраны')
