"""400 × 300 native one-bit, fixed-position sector dashboard."""
from datetime import datetime
from market_page import MonoCanvas, fit
from sectors import SECTORS
from weather_page import finish

def render_sectors(sectors):
    c=MonoCanvas()
    c.rounded((0,0,399,25),0,0)
    stamp=datetime.fromisoformat(sectors.fetched_at).strftime('%m月%d日 %H:%M:%S') if sectors.fetched_at else '等待有效报价'
    c.text('行情获取时间: '+stamp,10,7,13,255)
    lookup={row['code']:row for row in sectors.rows}
    for index,(name,code) in enumerate(SECTORS):
        col=index//7; row=index%7; x=8+col*196; y=31+row*38
        c.d.rectangle((x,y,x+4,y+28),fill=0)
        fit(c,name,x+10,y+7,15,91)
        quote=lookup.get(code)
        if quote and quote['traded']:
            rate=(quote['price']/quote['previous']-1)*100
            positive=rate>0
            c.d.rectangle((x+106,y,x+184,y+28),fill=0 if positive else 1,outline=0,width=2)
            fit(c,f'{rate:+.2f}%',x+145,y+7,15,72,fill=255 if positive else 0,anchor='center')
        else:
            c.d.rectangle((x+106,y,x+184,y+28),fill=1,outline=0,width=2)
            c.text('—',x+145,y+6,15,0,anchor='center')
    return finish(c).convert('L')
