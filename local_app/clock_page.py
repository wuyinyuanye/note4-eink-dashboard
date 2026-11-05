"""400x300 continuous-tone clock renderer for the NOTE4 local app.

Based on the official clock/weather INFORMATION STRUCTURE; not extracted
firmware artwork. Does not connect, publish, or change device configuration.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import json
import os
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Shanghai')
SCALE = 4


@dataclass(frozen=True)
class Weather:
    city: str = '天气'
    condition: str | None = None
    temperature: int | None = None
    low: int | None = None
    high: int | None = None
    tomorrow_condition: str | None = None
    tomorrow_low: int | None = None
    tomorrow_high: int | None = None


@dataclass(frozen=True)
class Status:
    battery: int | None = None
    wifi_connected: bool | None = None


def lunar_label(now: datetime) -> str:
    try:
        from lunardate import LunarDate
        lunar = LunarDate.from_solar_date(now.year, now.month, now.day)
        months = ['正','二','三','四','五','六','七','八','九','十','冬','腊']
        days = ['初一','初二','初三','初四','初五','初六','初七','初八','初九','初十',
                '十一','十二','十三','十四','十五','十六','十七','十八','十九','二十',
                '廿一','廿二','廿三','廿四','廿五','廿六','廿七','廿八','廿九','三十']
        return f'农历 {"闰" if lunar.is_leap_month else ""}{months[lunar.month-1]}月{days[lunar.day-1]}'
    except (ImportError, ValueError):
        return '农历暂不可用'


def font_path(kind: str) -> str:
    custom = os.environ.get('NOTE4_CLOCK_DIGIT_FONT' if kind == 'digits' else 'NOTE4_CLOCK_TEXT_FONT')
    candidates = [custom] if custom else []
    if kind == 'digits':
        candidates += ['/System/Library/Fonts/Supplemental/DIN Alternate Bold.ttf',
                       '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']
    else:
        candidates += ['/System/Library/Fonts/PingFang.ttc',
                       str(HERE.parent/'fonts/zfull_GB.ttf')]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise RuntimeError(f'请配置 {kind} 字体路径')


class Canvas:
    def __init__(self):
        self.im = Image.new('L', (400*SCALE,300*SCALE),255)
        self.d = ImageDraw.Draw(self.im)
        self.boxes = []

    def text(self, text, x, y, size, fill=15, digits=False, anchor='left'):
        f = ImageFont.truetype(font_path('digits' if digits else 'text'),round(size*SCALE))
        b = self.d.textbbox((0,0),text,font=f)
        w,h = b[2]-b[0],b[3]-b[1]
        px = x*SCALE-(w if anchor=='right' else w/2 if anchor=='center' else 0)
        py = y*SCALE
        if px<0 or px+w>400*SCALE or py<0 or py+h>300*SCALE:
            raise ValueError(f'文字越界：{text}')
        self.boxes.append((text,(px/SCALE,py/SCALE,(px+w)/SCALE,(py+h)/SCALE)))
        self.d.text((round(px-b[0]),round(py-b[1])),text,font=f,fill=fill)

    def line(self, points, fill=0, width=1):
        self.d.line([(round(x*SCALE),round(y*SCALE)) for x,y in points],fill=fill,width=round(width*SCALE))

    def rounded(self, box, radius, fill, outline=None, width=1):
        self.d.rounded_rectangle(tuple(round(v*SCALE) for v in box),radius=round(radius*SCALE),
                                 fill=fill,outline=outline,width=round(width*SCALE))

    def ellipse(self, box, fill):
        self.d.ellipse(tuple(round(v*SCALE) for v in box),fill=fill)

    def weather_art(self, box):
        art = Image.open(HERE/'assets/partly-cloudy.png').convert('RGBA')
        # Source artwork may have transparent padding. Use its actual alpha bounds.
        bounds=art.getchannel('A').getbbox()
        if bounds:
            art=art.crop(bounds)
        x,y,w,h=box
        art.thumbnail((round(w*SCALE),round(h*SCALE)),Image.Resampling.LANCZOS)
        pos=(round(x*SCALE+(w*SCALE-art.width)/2),round(y*SCALE+(h*SCALE-art.height)/2))
        self.im.paste(ImageOps.grayscale(art),pos,art.getchannel('A'))


def draw_status(c: Canvas,status: Status):
    # Unknown state stays visibly unknown; it is not rendered as connected/0%.
    if status.wifi_connected is True:
        for radius in [4,8,12]:
            c.d.arc(tuple(round(v*SCALE) for v in (303-radius,31-radius,303+radius,31+radius)),
                    220,320,fill=65,width=round(1.5*SCALE))
        c.ellipse((301.5,27,304.5,30),35)
    else:
        c.text('Wi-Fi —' if status.wifi_connected is None else '离线',281,17,11,85)
    c.rounded((335,16,359,28),2,255,70,1)
    c.rounded((360,20,362,24),0.5,70)
    if status.battery is not None:
        if not 0<=status.battery<=100:
            raise ValueError('电量应在0~100之间')
        if status.battery:
            c.rounded((337,18,337+20*status.battery/100,26),1,80)
    else:
        c.line([(344,22),(350,22)],100)
    c.text('—' if status.battery is None else str(status.battery),383,17,11,65,digits=True,anchor='right')


def render_clock(now: datetime,weather=Weather(),status=Status(),tasks=(),demo=False):
    if now.tzinfo is None:
        raise ValueError('时间必须包含时区')
    now=now.astimezone(TZ)
    c=Canvas()
    c.text(weather.city,18,16,14,40)
    if demo:
        c.text('设计预览',200,18,10,119,anchor='center')
    draw_status(c,status)
    # Fixed clock slot, stable colon and digit positions on every minute.
    value=now.strftime('%H:%M')
    for char,x in zip(value,[75,137,199,240,302]):
        if char==':':
            c.ellipse((196,75,202,81),38)
            c.ellipse((196,99,202,105),38)
        else:
            c.text(char,x,47,100,8,digits=True,anchor='center')
    date=f'{now.year}年{now.month}月{now.day}日  星期'+ '一二三四五六日'[now.weekday()]
    c.text(date,200,139,16,40,anchor='center')
    c.text(lunar_label(now),200,163,12,102,anchor='center')
    c.line([(18,188),(382,188)],202,0.5)
    # A quiet gray weather panel, with actual continuous-tone cloud artwork.
    c.rounded((18,196,382,289),9,238)
    c.text('今天',28,201,12,80)
    c.text(weather.condition or '天气未连接',187,201,12,65,anchor='right')
    c.line([(208,207),(208,274)],211,0.5)
    if weather.temperature is None:
        c.text('—',28,227,38,40,digits=True)
    else:
        c.text(str(weather.temperature)+'°',28,222,45,12,digits=True)
        if weather.condition=='多云':
            c.weather_art((100,212,95,63))
    limits='— / —' if weather.low is None or weather.high is None else f'{weather.low}° / {weather.high}°'
    c.text(limits,28,273,12,80,digits=True)
    if tasks:
        c.text('待办',226,201,12,80)
        for i,task in enumerate(tasks[:2]):
            c.rounded((226,227+i*25,234,235+i*25),1,255,110,1)
            # Fit the visible summary without mutating full task content.
            label=task if len(task)<=8 else task[:7]+'…'
            c.text(label,242,225+i*25,15,35)
    else:
        c.text('明天',226,201,12,80)
        c.text(weather.tomorrow_condition or '暂无预报',371,201,12,65,anchor='right')
        tomorrow='— / —' if weather.tomorrow_low is None or weather.tomorrow_high is None else f'{weather.tomorrow_low}° / {weather.tomorrow_high}°'
        c.text(tomorrow,226,233,28,25,digits=True)
        c.text('早晚温差 '+str(weather.tomorrow_high-weather.tomorrow_low)+'°' if weather.tomorrow_low is not None and weather.tomorrow_high is not None else '等待天气数据',226,273,12,90)
    for i,(label,box) in enumerate(c.boxes):
        for other,prior in c.boxes[:i]:
            if max(box[0],prior[0])<min(box[2],prior[2]) and max(box[1],prior[1])<min(box[3],prior[3]):
                raise ValueError(f'文字重叠：{label} / {other}')
    return c.im.resize((400,300),Image.Resampling.LANCZOS)


def simulate16(source):
    # Undithered local tonal study, not calibrated hardware emulation.
    # Send SOURCE to the SDK, which uses the device's actual linear palette.
    return source.point(lambda value: round(value/17)*17)


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--now',help='ISO8601 timestamp including offset; defaults to current China time')
    ap.add_argument('--demo',action='store_true',help='explicitly labeled example weather and status')
    ap.add_argument('--tasks',action='store_true',help='example tasks, requires --demo')
    ap.add_argument('--weather-json',type=Path,help='normalized Weather fields, not device/remote API')
    ap.add_argument('--out',type=Path,required=True)
    args=ap.parse_args()
    if args.tasks and not args.demo:
        ap.error('--tasks requires --demo')
    now=datetime.fromisoformat(args.now) if args.now else datetime.now(TZ)
    weather=Weather()
    status=Status()
    if args.demo:
        weather=Weather('杭州','多云',26,21,28,'晴间多云',20,27)
        status=Status(86,True)
    if args.weather_json:
        weather=Weather(**json.loads(args.weather_json.read_text()))
    im=render_clock(now,weather,status,('下午三点会议','记得带上雨伞') if args.tasks else (),args.demo)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    im.save(args.out)
    preview=simulate16(im)
    preview_path=args.out.with_stem(args.out.stem+'-16gray')
    preview.save(preview_path)
    preview.resize((800,600),Image.Resampling.NEAREST).save(args.out.with_stem(args.out.stem+'-2x'))
    print(json.dumps({'source':str(args.out),'preview':str(preview_path),'size':im.size,
                      'source_levels':len(im.getcolors(256)),'preview_levels':len(preview.getcolors(256)),
                      'lunar':lunar_label(now.astimezone(TZ))},ensure_ascii=False))


if __name__=='__main__':
    main()
