"""Standalone official-style switcher with a fixed inverse centre selection."""
from functools import lru_cache
from PIL import Image, ImageChops, ImageDraw, ImageOps
from market_page import MarketCanvas, MonoCanvas
from weather_page import finish

APPS = (('home', '天气', '此刻天气 · 七日预报'), ('album', '相册', '每日横屏 · 满屏展示'),
        ('market', '行情', '市场指数 · 自选证券'), ('sectors', '板块', '十四板块 · 黑白行情'), ('news', '新闻', '国内 · 国际各五条'),
        ('gold', '黄金', '上金所 · COMEX · 黄金ETF'), ('portfolio', '持仓', '成本 · 市值 · 浮动盈亏'))


def app_icon(c, kind, x, y, size=36):
    """Rounded 1.8px line icons, with curved silhouettes and balanced ink weight."""
    ratio = size / 40
    def point(xx, yy):
        return (round((x + xx * ratio) * c.scale), round((y + yy * ratio) * c.scale))
    def stroke(points, width=1.8, fill=0, closed=False):
        pixels = [point(xx, yy) for xx, yy in points]
        pen = max(1, round(width * ratio * c.scale))
        c.d.line(pixels, fill=c.ink(fill), width=pen, joint='curve')
        if not closed:
            radius = pen / 2
            for xx, yy in (pixels[0], pixels[-1]):
                c.d.ellipse((round(xx-radius), round(yy-radius), round(xx+radius), round(yy+radius)), fill=c.ink(fill))
    def ellipse(box, fill=None, outline=0, width=1.8):
        c.d.ellipse((*point(box[0],box[1]), *point(box[2],box[3])),
                    fill=c.ink(fill), outline=c.ink(outline), width=max(1,round(width*ratio*c.scale)))
    def curve(start, segments):
        points = [start]
        for a,b,end in segments:
            origin = points[-1]
            for step in range(1,13):
                t = step/12; u = 1-t
                points.append((u**3*origin[0]+3*u*u*t*a[0]+3*u*t*t*b[0]+t**3*end[0],
                               u**3*origin[1]+3*u*u*t*a[1]+3*u*t*t*b[1]+t**3*end[1]))
        return points

    if kind == 'home':
        ellipse((20,4,32,16))
        for a,b in [((26,0),(26,1)),((35,4),(37,2)),((36,11),(39,11))]:
            stroke([a,b],1.5)
        cloud = curve((9,31), [((2,31),(1,22),(7,20)),((9,18),(12,18),(14,20)),
                              ((14,11),(27,10),(30,19)),((36,17),(40,28),(33,31)),
                              ((28,32),(16,31),(9,31))])
        c.d.polygon([point(xx,yy) for xx,yy in cloud], fill=c.ink(255))
        stroke(cloud,closed=True)
    elif kind == 'album':
        back = curve((11,3), [((16,3),(27,3),(34,3)),((37,3),(38,4),(38,7))])
        stroke(back+[(38,27)],1.6)
        box = (x+4*ratio,y+7*ratio,x+33*ratio,y+35*ratio)
        c.rounded(box,4*ratio,255,0,1.8*ratio)
        ellipse((24,13,28,17),0,None)
        hills = curve((8,29), [((12,21),(14,20),(19,27)),((23,21),(26,20),(30,29))])
        stroke(hills,1.7)
    elif kind == 'market':
        stroke([(4,5),(4,36),(38,36)],1.5)
        trend = curve((10,29), [((14,29),(16,18),(21,22)),((27,24),(28,9),(37,7))])
        stroke(trend,2)
        stroke([(30,7),(37,7),(37,14)],1.8)
    elif kind == 'sectors':
        for xx,yy,hh in ((8,23,12),(17,16,19),(26,8,27)):
            c.rounded((x+xx*ratio,y+yy*ratio,x+(xx+5)*ratio,y+(yy+hh)*ratio),1*ratio,0)
        stroke([(5,36),(36,36)],1.5)
    elif kind == 'portfolio':
        c.rounded((x+5*ratio,y+4*ratio,x+35*ratio,y+36*ratio),4*ratio,255,0,1.8*ratio)
        stroke([(11,12),(29,12)],1.5)
        stroke([(11,19),(18,19),(18,28),(11,28),(11,19)],1.4)
        stroke([(22,19),(29,19)],1.5)
        stroke([(22,25),(29,25)],1.5)
    elif kind == 'gold':
        ellipse((5, 5, 35, 35), 255, 0, 1.8)
        ellipse((10, 10, 30, 30), 255, 0, 1.3)
        stroke([(15, 17), (20, 13), (25, 17), (23, 25), (17, 25), (15, 17)], 1.6)
        stroke([(17, 20), (23, 20)], 1.3)
    else:
        outline = curve((11,3), [((8,3),(7,4),(7,7))])
        outline += [(7,32)]
        outline += curve((7,32), [((7,35),(8,36),(11,36))])[1:]
        outline += [(31,36)]
        outline += curve((31,36), [((34,36),(35,35),(35,32))])[1:]
        outline += [(35,11),(27,3),(11,3)]
        stroke(outline,closed=True)
        stroke([(26,4),(26,12),(34,12)],1.5)
        for yy,right in ((18,29),(24,29),(30,23)):
            stroke([(13,yy),(right,yy)],1.7)


PANEL = (12, 93, 388, 211)
CARD_WIDTH = 116
CARD_LEFT = (400-CARD_WIDTH)//2
CARD_TOP = 99
CARD_BOTTOM = 205
CARD_PITCH = 128


@lru_cache(maxsize=8)
def _item_mask(index, mono=False):
    """Position-independent icon and title mask; focus controls ink polarity."""
    c = MonoCanvas() if mono else MarketCanvas()
    kind, title, _ = APPS[index]
    app_icon(c, kind, 179, 113, size=42)
    c.text(title, 200, 169, 20, 0, anchor='center')
    finish(c)
    return ImageOps.invert(c.im.convert('L')).crop(
        (CARD_LEFT*c.scale, CARD_TOP*c.scale,
         (CARD_LEFT+CARD_WIDTH)*c.scale, CARD_BOTTOM*c.scale))


def render_menu(selected, mono=False, offset=0):
    """Standalone selector: icons slide through a fixed black centre highlight."""
    if type(selected) is not int or not 0 <= selected < len(APPS):
        raise ValueError('应用选中项无效')
    if not isinstance(offset, (int, float)) or not -1 <= offset <= 1:
        raise ValueError('卡片位移必须在一格以内')
    c = MonoCanvas() if mono else MarketCanvas()
    focused = (selected + (1 if offset >= .5 else -1 if offset <= -.5 else 0)) % len(APPS)
    c.text('应用切换', 24, 26, 18, 0)
    c.text(f'{focused+1:02} / {len(APPS):02}', 376, 32, 11, 85, anchor='right')
    c.rounded(PANEL, 12, 255, 0, 1)
    focus = (CARD_LEFT, CARD_TOP, CARD_LEFT+CARD_WIDTH, CARD_BOTTOM)
    c.rounded(focus, 7, 0)

    track = Image.new('L', c.im.size, 0)
    for relative in range(-2,3):
        left = CARD_LEFT + (relative-offset)*CARD_PITCH
        track.paste(_item_mask((selected+relative)%len(APPS),mono),
                    (round(left*c.scale),CARD_TOP*c.scale))
    # Clip icons to the selector, leaving its rounded outside border intact.
    viewport = Image.new('L', c.im.size, 0)
    ImageDraw.Draw(viewport).rounded_rectangle(
        tuple(round(value*c.scale) for value in (13,94,387,210)),
        radius=11*c.scale, fill=255)
    track = ImageChops.multiply(track,viewport)
    ink = Image.new('L', c.im.size, 0)
    ImageDraw.Draw(ink).rounded_rectangle(
        tuple(round(value*c.scale) for value in focus), radius=7*c.scale, fill=255)
    c.im.paste(ink.convert(c.im.mode),(0,0),track)

    c.text(APPS[focused][2],200,238,12,0,anchor='center')
    c.line([(24,262),(376,262)],170,1)
    c.text('上键← / 下键→',24,278,11,85)
    c.text('确认进入',376,278,12,0,anchor='right')
    image = finish(c)
    return image.convert('L') if mono else image
