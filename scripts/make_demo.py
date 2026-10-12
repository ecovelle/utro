#!/usr/bin/env python3
"""Generate a clearly labelled preview using synthetic data only."""
import datetime as dt
import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from build_brief import build_data, render
from history import empty_history
from metrics import OZ_ART, WB_ART, normalize_oz, normalize_wb


def make_demo():
    state = empty_history()
    today = dt.date(2026, 10, 12)
    timestamp = '2026-10-12T05:10:00+03:00'
    state['run'] = {'today': today.isoformat(), 'generated': timestamp, 'status': {key: {'ok': True} for key in ['oz','wb','oz_stock','wb_stock','wb_prices','wb_feedback','oz_actions','wb_promos']}}
    state['coverage'] = {'oz': [['2024-10-12','2026-10-11']], 'wb': [['2026-07-15','2026-10-11']]}
    state['updated'] = {'oz': timestamp, 'wb': timestamp}
    day = dt.date(2024, 10, 12)
    while day < today:
        index = (day - dt.date(2024,10,12)).days
        for i, art in enumerate(OZ_ART):
            quantity = max(1, round(18 + math.sin(index/6+i)*8 + i*5 + (index%7)))
            status = 'cancelled' if (index+i)%17==0 else ('delivered' if day < today-dt.timedelta(days=4) else 'delivering')
            posting = {'posting_number': f'demo-{index}-{i}', 'created_at': day.isoformat()+'T10:00:00Z', 'status': status,
                       'products': [{'offer_id':art,'quantity':quantity,'price':str(3200+i*400),'currency_code':'KZT'}],
                       'analytics_data': {'city': ['Алматы','Москва','Астана','Санкт-Петербург','Казань','Не указан'][index%6]}, 'cancel_reason_id': 1}
            ident, row = normalize_oz(posting, 'fbs' if index%5==0 else 'fbo')
            state['oz'][ident] = row
        if day >= dt.date(2026,7,15):
            for i, art in enumerate(WB_ART):
                q = max(1, round(8+math.sin(index/7+i)*5+i*2))
                for n in range(q):
                    srid = f'demo-{index}-{i}-{n}'
                    is_cancel = (index+n)%13==0
                    source = {'srid':srid,'date':day.isoformat()+'T10:00:00','supplierArticle':art,'priceWithDisc':504+i*40,
                              'lastChangeDate':day.isoformat()+'T12:00:00','isCancel':is_cancel,
                              'cancelDate':day.isoformat()+'T12:00:00' if is_cancel else None,
                              'countryName': ['Россия','Казахстан','Беларусь','Россия'][index%4]}
                    ident, row = normalize_wb(source, 'orders')
                    state['wb_orders'][ident] = row
                    if not is_cancel and day+dt.timedelta(days=3)<today:
                        source.update(saleID='S'+srid,date=(day+dt.timedelta(days=3)).isoformat()+'T12:00:00',forPay=400.33+i*30)
                        ident,row=normalize_wb(source,'sales');state['wb_sales'][ident]=row
                        if (index+n)%41==0 and day+dt.timedelta(days=7)<today:
                            source.update(saleID='R'+srid,date=(day+dt.timedelta(days=7)).isoformat()+'T12:00:00')
                            ident,row=normalize_wb(source,'sales');state['wb_sales'][ident]=row
        day += dt.timedelta(days=1)
    state['snapshots'] = {
        'oz_stock': {'asOf':timestamp,'data':[{'sku':art,'warehouse':'Алматы','free':count,'promised':0,'reserved':0} for art,count in zip(OZ_ART,[740,290,1370])]},
        'wb_stock': {'asOf':timestamp,'data':{art:{'total':count,'returning':40,'toClient':60,'wh':{'Котовск':count}} for art,count in zip(WB_ART,[940,750,960,550])}},
        'wb_prices': {'asOf':timestamp,'data':{art:{'final':504+i*40}for i,art in enumerate(WB_ART)}},
        'oz_actions':{'asOf':timestamp,'data':[]}, 'wb_promos':{'asOf':timestamp,'data':[]}}
    return state


if __name__ == '__main__':
    data = build_data(make_demo())
    data['demo'] = True
    html = render(data).replace('<header class="top">', '<div class="banner"><b>Демонстрация дизайна.</b> Все числа вымышлены. Это не данные вашего кабинета.</div><header class="top">')
    target = Path(__file__).resolve().parents[1] / 'preview' / 'index.html'
    target.parent.mkdir(exist_ok=True)
    target.write_text(html,encoding='utf-8')
    print(target)
