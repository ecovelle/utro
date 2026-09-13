#!/usr/bin/env python3
"""Собирает brief_data.json из сырых выгрузок и вклеивает в шаблон brief.html → utro-ecovelle-plain.html (далее encrypt_page.py).
Входные файлы (в этой же папке):
  oz_raw.json   daily:[[date,sku,ordered,rev,fbo,fbs,deliveredByStatus]] (без отменённых), deliv:{date:{n,acc,amt}} (выкупы по finance),
                fin:{YYYY-MM:{acc,comm,log,amt,n}}, other:{YYYY-MM:{name:{n,amt}}}, month:{YYYY-MM:{ordered,rev,canc,cancRev,deliveredStatus,transit,fbo,fbs,postings}},
                transit:{fbo:{postings,units,sum},fbs:{...}}, cancels:{reason:n}, geo:[[city,{q,fbo,fbs}]], kzCities, whs, dtype, stock, actions
  wb_raw.json   daily:[[date,art,q,rev,canc]], sales:[[date,art,buy,forPay,rev]], month:{YYYY-MM:{ordered,rev,canc,cancRev,buy,pay}}, countries, regions, transit:{n,sum,byArt}, lastOrder
  wb_stock.json {art:{toClient,returning,total,wh:{name:qty}}}
  wb_feedback.json, wb_promos.json, wb_prices.json
Запуск: python3 build_brief.py [YYYY-MM-DD]
"""
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
  "cancY": ozm.get(cur_m,{}).get("canc",0),  # отмен за месяц (по дням Ozon не отдаёт после фильтра)
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

json.dump(D, open(f'{HERE}/brief_data.json','w',encoding='utf-8'), ensure_ascii=False)
html = open(f'{HERE}/brief.html', encoding='utf-8').read().replace('__DATA__', json.dumps(D, ensure_ascii=False).replace('</','<\\/'))
open(f'{HERE}/utro-ecovelle-plain.html','w',encoding='utf-8').write(html)
print('ok', today, 'oz y', D["oz"]["y"], 'wb y', D["wb"]["y"])
