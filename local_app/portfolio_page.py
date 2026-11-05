"""Continuous-gray portfolio page for the NOTE4."""
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from clock_page import TZ
from market_page import MarketCanvas, market_font
from weather_page import finish
from portfolio import calculate


def money(value, signed=False):
    value=value.quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
    return format(value,'+,.2f' if signed else ',.2f')


def fit(c,text,x,y,size,width,fill=34,anchor='left'):
    while size>12 and market_font(size,c.scale).getlength(text)>width*c.scale: size-=1
    if market_font(size,c.scale).getlength(text)>width*c.scale: raise ValueError('持仓数值过长')
    c.text(text,x,y,size,fill,anchor=anchor)


def render_portfolio(portfolio):
    c=MarketCanvas();c.rounded((0,0,400,32),0,34)
    c.text('持仓账本',13,6,19,255)
    if not portfolio.config:
        c.text('尚未设置持仓',200,116,21,68,anchor='center')
        c.text('请在控制页填写代码、份额和成本',200,152,12,119,anchor='center')
        return finish(c)
    config=portfolio.config;c.text(config['code'][2:],387,11,12,221,anchor='right')
    quote=portfolio.quote
    if not quote:
        c.text('等待有效报价',200,112,20,68,anchor='center')
        c.text('确认键更新 · 失败时保留上次画面',200,150,12,119,anchor='center')
        return finish(c)
    values=calculate(config,quote['price'])
    c.text(quote['name'][:14]+' · 浮动盈亏',14,45,13,102)
    fit(c,money(values['pnl'],True),14,65,34,238,20)
    fit(c,money(values['return_pct'],True)+'%',386,76,22,126,68,'right')
    c.line([(14,111),(386,111)],187,1)
    c.rounded((10,119,390,219),5,241)
    c.line([(200,126),(200,211)],204,1);c.line([(20,169),(380,169)],204,1)
    rows=(('持有份额',format(Decimal(config['quantity']),',f')+' 份',20,127),
          ('成本单价',format(Decimal(config['cost_price']),'f'),216,127),
          ('行情现价',format(Decimal(quote['price']),'f'),20,176),
          ('持仓成本',money(values['cost_total']),216,176))
    for label,value,x,y in rows:
        c.text(label,x,y,11,102);fit(c,value,x,y+16,20,169)
    c.text('持仓市值',14,236,14,68)
    fit(c,money(values['market_value'])+' 元',386,229,24,255,20,'right')
    c.rounded((0,276,400,300),0,51)
    stamp=datetime.fromisoformat(quote['stamp']).astimezone(TZ).strftime('%m-%d %H:%M')
    c.text(quote['source']+' '+stamp,12,282,11,238)
    c.text('未扣卖出费',387,282,11,238,anchor='right')
    return finish(c)
