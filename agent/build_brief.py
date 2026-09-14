#!/usr/bin/env python3
"""Собирает brief_data.json из сырых выгрузок и вклеивает в шаблон brief.html → utro-ecovelle-plain.html."""
import json, sys, datetime as dt, os
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
def L(n):
    try: return json.load(open(f'{HERE}/{n}', encoding='utf-8'))
    except Exception: return {}
OZ, WB, WS, FB, PR, PRICES = (L(n) for n in ('oz_raw.json','wb_raw.json','wb_stock.json','wb_feedback.json','wb_promos.json','wb_prices.json'))

now = dt.datetime.now(ZoneInfo('Asia/Shanghai'))
today = dt.date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else now.date()
days = [(today - dt.timedelta(days=i)).isoformat() for i in range(30, 0, -1)]
y, y2, w, pw = days[-1], days[-2], days[-7:], days[-14:-7]
cur_m = today.strftime('%Y-%m'); prev_m = (today.replace(day=1) - dt.timedelta(days=1)).strftime('%Y-%m')
days_in_cur = (today - today.replace(day=1)).days  # полных дней текущего месяца (до вчера)
NAMES = {"ecovelle_245":"Дневные","ecovelle_290":"Ночные","ecovelle_155":"Ежедневные",
         "Ecovelle245":"Дневные","Ecovelle290":"Ночные","ecovelle155":"Ежедневные","EcovelleMix":"Микс (4 пачки)"}
OZ_SKU = ["ecovelle_245","ecovelle_290","ecovelle_155"]; WB_ART = ["Ecovelle245","Ecovelle290","ecovelle155","EcovelleMix"]

def S(rows, ds, i, art=None): return sum(x[i] for x in rows if x[0] in ds and (art is None or x[1] == art))
def series(rows, iq, irev, ic=None):
    out = {d: [d,0,0,0] for d in days}
    for x in rows:
        if x[0] in out:
            out[x[0]][1] += x[iq]; out[x[0]][2] += x[irev]
            if ic is not None: out[x[0]][3] += x[ic]
    return list(out.values())

D = {"generated": now.isoformat(timespec='minutes'), "today": today.isoformat(), "yesterday": y, "curMonth": cur_m, "prevMonth": prev_m, "daysInCur": days_in_cur}

# ---------- OZON ----------
ozd = OZ["daily"]; deliv = OZ.get("deliv", {})
oz_deliv_series = [[d, deliv.get(d,{}).get("n",0), deliv.get(d,{}).get("amt",0), deliv.get(d,{}).get("acc",0)] for d in days]
def oz_period(ds):
    dl = [deliv[d] for d in ds if d in deliv]
    return {"ordered": S(ozd, ds, 2), "rev": S(ozd, ds, 3), "fbo": S(ozd, ds, 4), "fbs": S(ozd, ds, 5),
            "delivered": sum(x["n"] for x in dl), "payout": sum(x["amt"] for x in dl), "accrued": sum(x["acc"] for x in dl)}
ozm = OZ.get("month", {}); ozfin = OZ.get("fin", {}); ozother = OZ.get("other", {})
def oz_month(m):
    M = ozm.get(m, {}); F = ozfin.get(m, {}); O_ = ozother.get(m, {})
    fees = sum(v["amt"] for v in O_.values())
    return {"ordered": M.get("ordered",0), "rev": M.get("rev",0), "canc": M.get("canc",0), "cancRev": M.get("cancRev",0),
            "fbo": M.get("fbo",0), "fbs": M.get("fbs",0), "delivered": F.get("n",0), "accrued": F.get("acc",0),
            "commission": F.get("comm",0), "logistics": F.get("log",0), "payout": F.get("amt",0), "fees": fees, "feesDetail": O_,
            "transit": M.get("transit",0)}
kz = set(OZ.get("kzCities", []))
geo = OZ.get("geo", [])
geo_known = [g for g in geo if not g[0].startswith('(')]
geo_unknown = sum(g[1]["q"] for g in geo if g[0].startswith('('))
kz_q = sum(g[1]["q"] for g in geo_known if g[0] in kz); ru_q = sum(g[1]["q"] for g in geo_known if g[0] not in kz)
D["oz"] = {
  "daily": series(ozd, 2, 3), "deliv": oz_deliv_series,
  "y": oz_period([y]), "y2": oz_period([y2]), "w": oz_period(w), "pw": oz_period(pw),
  "cancY": ozm.get(cur_m,{}).get("canc",0),
  "month": {cur_m: oz_month(cur_m), prev_m: oz_month(prev_m)},
  "transit": OZ.get("transit", {}), "cancels": OZ.get("cancels", {}),
  "geo": {"cities": geo_known[:15], "unknown": geo_unknown, "kz": kz_q, "ru": ru_q, "whs": OZ.get("whs", {}), "dtype": OZ.get("dtype", {})},
  "actions": OZ.get("actions", []),
}
# остатки Ozon
rate = {k: S(ozd, w, 2, k)/7 for k in OZ_SKU}; ozst = []
for k in OZ_SKU:
    rows = [r for r in OZ.get("stock", []) if r["o"] == k]; tot = sum(r["free"] for r in rows); prom = sum(r["promised"] for r in rows)
    ozst.append({"sku":k,"name":NAMES[k],"free":tot,"promised":prom,"rate":round(rate[k],1),"days": round((tot+prom)/rate[k]) if rate[k] else None,
                 "wh":[{"wh":r["wh"].replace("_РФЦ","").replace("_"," ").title(),"free":r["free"],"promised":r["promised"]} for r in rows]})

# ---------- WB ----------
wbd = WB["daily"]; wbs = WB["sales"]
def wb_period(ds):
    return {"ordered": S(wbd, ds, 2), "rev": S(wbd, ds, 3), "canc": S(wbd, ds, 4), "buy": S(wbs, ds, 2), "payout": S(wbs, ds, 3), "buyRev": S(wbs, ds, 4)}
wbm = WB.get("month", {})
def wb_month(m):
    M = wbm.get(m, {})
    return {"ordered": M.get("ordered",0), "rev": M.get("rev",0), "canc": M.get("canc",0), "cancRev": M.get("cancRev",0), "delivered": M.get("buy",0), "payout": M.get("pay",0)}
def best14(art):
    return round(max(S(wbd, days[i:i+14], 2, art)/14 for i in range(0, 17)), 1)
wbst = []
for a in WB_ART:
    r14 = best14(a); S_ = WS.get(a, {"total":0,"returning":0,"toClient":0,"wh":{}})
    wbst.append({"sku":a,"name":NAMES[a],"free":S_["total"],"returning":S_["returning"],"toClient":S_["toClient"],"rate14":r14,
                 "days": round(S_["total"]/r14) if r14 else None, "wh":[{"wh":k,"free":v} for k,v in S_["wh"].items()]})
D["wb"] = {
  "daily": series(wbd, 2, 3, 4), "buy": series(wbs, 2, 3),
  "y": wb_period([y]), "y2": wb_period([y2]), "w": wb_period(w), "pw": wb_period(pw),
  "month": {cur_m: wb_month(cur_m), prev_m: wb_month(prev_m)},
  "transit": WB.get("transit", {}), "returning": sum(v.get("returning",0) for v in WS.values()),
  "geo": {"countries": WB.get("countries", []), "regions": WB.get("regions", [])[:15]},
  "prices": PRICES, "feedback": FB, "promos": PR, "lastOrder": WB.get("lastOrder", {}),
}
D["stock"] = {"oz": ozst, "wb": wbst}
D["names"] = NAMES


# ---------- «Требует внимания»: правила (вариант без ИИ) ----------
def _d(n):
    x = today + dt.timedelta(days=n)
    M = ["января","февраля","марта","апреля","мая","июня","июля","августа","сентября","октября","ноября","декабря"]
    return f"{x.day} {M[x.month-1]}"

def _f(n):
    return f"{round(n):,}".replace(",", " ")

def rule_items(D, OZ):
    it = []                      # (ранг, item) — 0 crit, 1 warn, 2 info
    O, W = D["oz"], D["wb"]
    om_, op_ = O["month"][cur_m], O["month"][prev_m]
    wm_, wp_ = W["month"][cur_m], W["month"][prev_m]
    st = D["stock"]
    P = W.get("prices") or {}
    cash = (O.get("cash") or {}).get(cur_m) or {}

    # 1. цена WB вне рабочего коридора
    bad = [a for a, v in P.items() if v.get("final") and v.get("normal") and v["final"] > v["normal"] * 2]
    if bad:
        ex = P.get("Ecovelle245") or P[bad[0]]
        it.append((0, {"c": "crit", "h": "Wildberries стоит из-за цены", "w": f"вчера заказов — {W['y']['ordered']}",
            "p": f"Цена на витрине <b>{_f(ex['final'])} ₽</b> за пачку вместо рабочих ~{ex['normal']} ₽ — так по {len(bad)} карточкам из {len(P)}. "
                 f"За месяц <b>{_f(wm_['ordered'])} заказов</b> против {_f(wp_['ordered'])} в прошлом. Выкупы ({_f(wm_['delivered'])}) — хвост старых заказов.",
            "a": "Вернуть цену в WB → «Цены и скидки» на всех карточках. Пока цена такая, ни акции, ни реклама не работают."}))

    # 2. запас Ozon
    for r in st["oz"]:
        if r["days"] is None:
            continue
        wh = " · ".join(f"{w['wh']} {_f(w['free'])}" for w in r["wh"]) or "нет остатка"
        if r["days"] < 14:
            it.append((0, {"c": "crit", "h": f"{r['name']} на Ozon — {r['days']} дней запаса", "w": "критично",
                "p": f"Свободно <b>{_f(r['free'])} шт</b> ({wh}) при темпе {str(r['rate']).replace('.', ',')} шт в день.",
                "a": f"Поставка нужна сейчас: остаток кончится около {_d(r['days'])}, приёмка на складе Ozon занимает неделю-полторы."}))
        elif r["days"] < 45:
            it.append((1, {"c": "warn", "h": f"{r['name']} на Ozon — {r['days']} дней запаса", "w": "пора планировать",
                "p": f"Свободно <b>{_f(r['free'])} шт</b> ({wh}) при темпе {str(r['rate']).replace('.', ',')} шт в день.",
                "a": f"Запустить поставку: при нынешнем темпе остаток кончится около {_d(r['days'])}."}))
        elif r["days"] > 150:
            it.append((2, {"c": "info", "h": f"{r['name']} на Ozon — избыток, {r['days']} дней", "w": "деньги стоят на складе",
                "p": f"Свободно <b>{_f(r['free'])} шт</b> ({wh}) при темпе {str(r['rate']).replace('.', ',')} шт в день.",
                "a": "Новую поставку по этой позиции не заказывать, пока запас не опустится ниже 90 дней."}))

    # 3. перекос по складам Ozon: склад везёт много, а товара на нём нет
    whs = O["geo"]["whs"]
    tot = sum(whs.values()) or 1
    for raw, q in sorted(whs.items(), key=lambda kv: -kv[1]):
        if q / tot < 0.15:
            continue
        nice = raw.replace("_РФЦ", "").replace("_", " ").title()
        empty = [r["name"] for r in st["oz"]
                 if r["free"] and not any(w["free"] for w in r["wh"] if w["wh"] == nice)]
        if empty:
            it.append((1, {"c": "warn", "h": f"На складе «{nice}» нет позиций: {', '.join(empty)}", "w": "перекос по складам",
                "p": f"Через {nice} ушло <b>{_f(q)}</b> из {_f(tot)} упаковок FBO ({q/tot*100:.0f} %), но этих позиций там <b>0</b> — "
                     f"они лежат на других складах. Покупатель ждёт дольше, часть заказов теряется на сроке доставки.",
                "a": f"Перебросить часть остатка на {nice}. Товар есть, вопрос только в распределении."}))
        break

    # 4. возвраты WB
    ret = sum(r["returning"] for r in st["wb"])
    free = sum(r["free"] for r in st["wb"])
    if ret > max(free, 1) * 2:
        det = ", ".join(f"{r['name']} {_f(r['returning'])}" for r in sorted(st["wb"], key=lambda r: -r["returning"]) if r["returning"])
        it.append((1, {"c": "warn", "h": f"{_f(ret)} упаковок едут возвратами на склады WB", "w": f"в {ret/max(free,1):.1f} раза больше остатка".replace(".", ","),
            "p": f"Невыкупы возвращаются: {det}. Свободный остаток при этом <b>{_f(free)} шт</b>. Каждая обратная доставка платная.",
            "a": f"После приёмки остаток вырастет примерно до {_f(free+ret)} шт — успеть вернуть рабочую цену к этому моменту, иначе товар ляжет на хранение."}))

    # 5. услуги Ozon съели больше комиссии
    if cash and cash.get("orders"):
        serv, comm, orders = -cash.get("serv", 0), -cash.get("comm", 0), cash["orders"]
        if serv > comm:
            it.append((1, {"c": "warn", "h": "Услуги Ozon съели больше комиссии", "w": "по всему кабинету",
                "p": f"По выписке cash-flow: продажи <b>{_f(orders)} ₸</b>, комиссия −{_f(comm)} ₸ ({comm/orders*100:.0f} %), "
                     f"доставка и возвраты −{_f(-cash.get('deliv',0))} ₸, а «услуги» — <b>−{_f(serv)} ₸</b> ({serv/orders*100:.0f} % оборота). "
                     f"Расшифровки в API нет: Ozon отключил метод финансовых транзакций.",
                "a": "Открыть Ozon → Финансы → Начисления и посмотреть, из чего сложились услуги: продвижение, кросс-докинг, обработка излишков, штрафы."}))

    # 6. схема отгрузки внезапно отвалилась
    if op_["fbs"] and not om_["fbs"]:
        it.append((2, {"c": "info", "h": "FBS не отгружает весь месяц", "w": f"в прошлом месяце — {_f(op_['fbs'])} упаковок",
            "p": f"Со своего склада отгружено <b>0</b>: все {_f(om_['ordered'])} упаковок ушли со складов Ozon. Причина не видна из API — "
                 f"либо склад выключен в кабинете, либо на нём нет остатка.",
            "a": "Проверить в настройках Ozon, включён ли собственный склад и есть ли на нём остаток."}))

    # 7. акции: не подключённые бустинги Ozon + старты WB в ближайшие 7 дней
    tstr, soon = today.isoformat(), (today + dt.timedelta(days=7)).isoformat()
    off = [a for a in O.get("actions", []) if a["to"] >= tstr and not a["part"]]
    up = [p for p in W.get("promos", []) if tstr <= p["start"] <= soon]
    if off or up:
        parts = []
        if off:
            parts.append("Ozon: " + "; ".join(f"«{a['title'].split('.')[0]}» до {a['to'][8:10]}.{a['to'][5:7]} ({a['pot']} товаров)" for a in off[:3]) + " — участие не подключено")
        if up:
            parts.append("WB стартуют в ближайшие 7 дней: " + "; ".join(f"«{p['name'].split('(')[0].strip()}» с {p['start'][8:10]}.{p['start'][5:7]}" for p in up[:3]))
        it.append((2, {"c": "info", "h": "Акции", "w": f"{len(off)} без участия, {len(up)} на старте",
            "p": ". ".join(parts) + ".",
            "a": "Подключить бустинг на Ozon — он даёт показы без скидки на цену. По WB проверить, что автоакции не срежут цену ниже себестоимости."}))

    # 8. отзывы WB
    fb = W.get("feedback") or {}
    s = fb.get("sinceAug7") or {}
    if fb.get("unansweredTotal", 0) > 100:
        dist = s.get("dist") or {}
        low = sum(dist.get(k, 0) for k in ("1", "2", "3"))
        th = ", ".join(t[0] for t in (fb.get("themes") or [])[:5]) or "—"
        it.append((1, {"c": "warn", "h": f"{_f(fb['unansweredTotal'])} отзывов WB без ответа", "w": "средняя " + f"{s.get('avg',0):.2f}".replace(".", ",") + " ★",
            "p": f"За 30 дней {s.get('n',0)} отзывов, из них {low} с оценкой 1–3 ★. Чаще всего пишут про: <b>{th}</b>.",
            "a": "Ответить хотя бы на 1–3 ★ за последние две недели. Отзывы про чужие товары отправить в поддержку WB на удаление."}))

    it.sort(key=lambda x: x[0])
    return [x[1] for x in it[:8]]


D["oz"]["cash"] = OZ.get("cash", {})

import os as _os
_ov = f'{HERE}/items_override.json'
D["items"] = json.load(open(_ov, encoding='utf-8')) if _os.path.exists(_ov) else rule_items(D, OZ)

json.dump(D, open(f'{HERE}/brief_data.json','w',encoding='utf-8'), ensure_ascii=False)
html = open(f'{HERE}/brief.html', encoding='utf-8').read().replace('__DATA__', json.dumps(D, ensure_ascii=False).replace('</','<\\/'))
open(f'{HERE}/utro-ecovelle-plain.html','w',encoding='utf-8').write(html)
print('ok', today, 'oz y', D["oz"]["y"], 'wb y', D["wb"]["y"])
