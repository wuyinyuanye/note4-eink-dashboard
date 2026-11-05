"""Fixed labels for Gray4: normal quote changes touch only numeric columns."""
from PIL import Image,ImageDraw,ImageFont
from datetime import datetime
from functools import lru_cache
from clock_page import Canvas,HERE,SCALE
from weather_page import finish


MARKET_FONT=HERE.parent/'fonts/zfull_GB.ttf'


@lru_cache(maxsize=64)
def market_font(size,scale=1):
    return ImageFont.truetype(str(MARKET_FONT),round(size*scale))


class MarketCanvas(Canvas):
    scale=SCALE

    @staticmethod
    def ink(value): return value

    def text(self,text,x,y,size,fill=15,digits=False,anchor='left'):
        scale=self.scale;f=market_font(size,scale)
        b=self.d.textbbox((0,0),text,font=f);w,h=b[2]-b[0],b[3]-b[1]
        px=x*scale-(w if anchor=='right' else w/2 if anchor=='center' else 0);py=y*scale
        if px<0 or px+w>400*scale or py<0 or py+h>300*scale:raise ValueError(f'文字越界：{text}')
        self.boxes.append((text,(px/scale,py/scale,(px+w)/scale,(py+h)/scale)))
        self.d.text((round(px-b[0]),round(py-b[1])),text,font=f,fill=self.ink(fill))


class MonoCanvas(MarketCanvas):
    """Native-size binary drawing preserves small glyphs without threshold specks."""
    def __init__(self):
        self.im=Image.new('1',(400,300),1);self.d=ImageDraw.Draw(self.im);self.boxes=[]
    scale=1

    @staticmethod
    def ink(value):
        return None if value is None else 1 if value>=240 else 0

    def line(self,points,fill=0,width=1):
        self.d.line(points,fill=self.ink(fill),width=max(1,round(width)))

    def rounded(self,box,radius,fill,outline=None,width=1):
        self.d.rounded_rectangle(box,radius=radius,fill=self.ink(fill),outline=self.ink(outline),width=max(1,round(width)))


def render_market(market, now, demo=False,mono=False):
    c = MonoCanvas() if mono else MarketCanvas()
    c.rounded((0, 0, 400, 30), 0, 20)
    c.text('自选行情', 12, 9, 13, 255)
    c.text('模拟数据' if demo else f'每{market.interval//60}分钟更新', 388, 9, 12, 255, anchor='right')
    c.text('名称 / 代码', 12, 41, 12 if mono else 11, 90)
    c.text('现价 / 点位', 272, 41, 12 if mono else 11, 90, anchor='right')
    c.text('涨跌幅', 388, 41, 12 if mono else 11, 90, anchor='right')
    c.line([(12, 59), (388, 59)], 150, .5)
    lookup = {r['code']: r for r in market.rows}
    visible = market.codes[market.page*6:(market.page+1)*6]
    roomy = len(visible) <= 4
    row_height = min(64, 192//max(1, len(visible)))
    for i, code in enumerate(visible):
        y = 67+i*row_height
        row = lookup.get(code)
        fit(c, row['name'] if row else code, 12, y+3 if roomy else y, 18 if roomy else 14, 174)
        c.text(code, 12, y+(29 if roomy else 17), 11 if mono else 9, 100)
        if row:
            price = format(row['price'], '.2f' if code.startswith(('sh000', 'sz399')) else '.3f')
            fit(c, price, 272, y+(9 if roomy else 4), 22 if roomy else 18, 78, digits=True, anchor='right')
            rate = (row['price']/row['previous']-1)*100 if row['traded'] else None
            positive = rate is not None and rate > 0
            c.rounded((284, y+(3 if roomy else 0), 388, y+(37 if roomy else 25)), 3, 25 if positive else 255, 60, .5)
            fit(c, f'{rate:+.2f}%' if rate is not None else '未成交', 336, y+(11 if roomy else 5), 19 if roomy else 16, 96,
                fill=255 if positive else 25, anchor='center')
        else:
            c.text('—', 272, y+5, 17, 100, anchor='right')
            c.text('—', 344, y+5, 17, 100, anchor='center')
        c.line([(12, y+row_height-3), (388, y+row_height-3)], 220, .5)
    statuses={market.row_status(row,now) for row in market.rows}
    updated=datetime.fromisoformat(market.fetched_at).strftime('%m-%d %H:%M:%S') if market.fetched_at else '尚未更新'
    prefix='模拟数据 · ' if demo else '更新失败 · ' if market.error else '历史报价 · ' if '历史' in statuses else '报价延迟 · ' if '延迟' in statuses else ''
    c.text(prefix+'更新 '+updated,12,279,11,90)
    pages = (len(market.codes)+5)//6
    c.text(f'{market.page+1} / {pages}', 388, 279, 11, 90, anchor='right')
    return finish(c).convert('L') if mono else finish(c)


def fit(c,text,x,y,size,max_width,**kwargs):
    while size>9 and market_font(size,c.scale).getlength(text)>max_width*c.scale:
        size-=1
    c.text(text,x,y,size,**kwargs)
