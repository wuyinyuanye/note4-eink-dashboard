"""Dynamic B composition. Continuous tones are sent to the SDK exactly once."""
from datetime import datetime
from functools import lru_cache
import json
from PIL import Image, ImageDraw, ImageOps
from clock_page import Canvas, SCALE, HERE, TZ, lunar_label
from weather import CODES, timestamp

HOME_BATTERY_BOX=(326,14,385,32)


ICON_ROOT=HERE/'assets/weather-realistic-v1'
ICON_SET=json.loads((ICON_ROOT/'manifest.json').read_text())


def art_name(code, day):
    if not isinstance(code,(int,float)) or code!=int(code): return None
    condition=ICON_SET['conditions'].get(str(int(code)))
    return condition['day' if day else 'night'] if condition else None


@lru_cache(maxsize=16)
def artwork(name):
    spec=ICON_SET['assets'].get(name)
    if not spec: return None
    path=ICON_ROOT/spec['file']
    if not path.is_file(): return None
    art=Image.open(path).convert('RGBA')
    bounds=art.getchannel('A').getbbox()
    return art.crop(bounds) if bounds else art


def weather_art(c,code,day,box=(26,7,368,231)):
    name=art_name(code,day)
    art=artwork(name) if name else None
    if art is None:
        x,y,w,h=box
        fit(c,CODES.get(code,'天气未知'),x+w/2,y+h/2-8,min(24,h*.3),w-6,fill=100,anchor='center')
        return
    if box==(26,7,368,231):
        box=ICON_SET['assets'][name].get('hero_box',box)
    x,y,w,h=box
    art=art.copy();art.thumbnail((round(w*SCALE),round(h*SCALE)),Image.Resampling.LANCZOS)
    c.im.paste(ImageOps.grayscale(art),(round(x*SCALE+(w*SCALE-art.width)/2),round(y*SCALE+(h*SCALE-art.height)/2)),art.getchannel('A'))


def finish(c):
    for i,(label,box) in enumerate(c.boxes):
        for other,prior in c.boxes[:i]:
            if max(box[0],prior[0])<min(box[2],prior[2]) and max(box[1],prior[1])<min(box[3],prior[3]):
                raise ValueError(f'文字重叠：{label} / {other}')
    return c.im.resize((400,300),Image.Resampling.LANCZOS)


def fit(c,text,x,y,size,max_width,**kwargs):
    from PIL import ImageFont
    from clock_page import font_path
    while size>9:
        f=ImageFont.truetype(font_path('digits' if kwargs.get('digits') else 'text'),round(size*SCALE))
        if f.getlength(text)/SCALE<=max_width: break
        size-=1
    c.text(text,x,y,size,**kwargs)


def draw_battery(c, status):
    status=status or {}
    percent=status.get('battery_percent')
    if percent is not None and (isinstance(percent,bool) or not isinstance(percent,int) or not 0<=percent<=100):
        raise ValueError('电量应为0到100之间的整数或未知')
    c.rounded((328,17,351,28),2,None,90,1)
    c.rounded((352,20,354,25),0.6,90)
    if percent is None:
        c.line([(336,22.5),(343,22.5)],115,1)
    elif percent>0:
        c.rounded((330,19,330+19*percent/100,26),0.7,100)
    if status.get('charging') is True:
        c.d.polygon([(round(x*SCALE),round(y*SCALE)) for x,y in
                     [(340,18),(336,23),(339,23),(338,27),(343,21),(340,21)]],fill=35)
    c.text('—' if percent is None else f'{percent}%',381,18,11,85,digits=True,anchor='right')


def render(now, snapshot, freshness, page='home', city='天气', home_frame=True, status=None):
    if now.tzinfo is None: raise ValueError('时间必须包含时区')
    now=now.astimezone(TZ)
    c=Canvas()
    if page=='home':
        if snapshot:
            current=snapshot['current']
            weather_art(c,current['weather_code'],current['is_day'])
        else:
            c.text('等待天气数据',200,110,26,110,anchor='center')
        mask=Image.new('L',c.im.size,0);d=ImageDraw.Draw(mask)
        for y in range(300*SCALE):
            d.line((0,y,400*SCALE,y),fill=round(max(0,min(1,(y/SCALE-190)/75))*255))
        c.im=Image.composite(Image.new('L',c.im.size,255),c.im,mask);c.d=ImageDraw.Draw(c.im)
        c.text(f'{now.month}月{now.day}日 · 星期'+ '一二三四五六日'[now.weekday()],19,16,13,30)
        draw_battery(c,status)
        # Transparent outline groups the numbers without masking the cloud below it.
        if home_frame:
            scene=c.im.copy()
            c.rounded((16,198,384,275),10,None,195,0.75)
            # Let the rendered cloud sit in front of the outline, including wispy edges.
            foreground=scene.point(lambda value: min(255,(255-value)*16))
            c.im=Image.composite(scene,c.im,foreground)
            c.d=ImageDraw.Draw(c.im)
        temp='—' if not snapshot else f"{round(snapshot['current']['temperature_2m'])}°"
        fit(c,temp,29,211,48,160,fill=72,digits=True)
        if snapshot:
            current=snapshot['current']
            summary=f"体感 {round(current['apparent_temperature'])}° · 湿度 {current['relative_humidity_2m']:g}%"
            fit(c,summary,29,257,11,215,fill=100)
        if snapshot:
            day=next((v for v in snapshot['daily'] if v['time']==now.date().isoformat()),None)
            limits=f"{round(day['temperature_2m_min'])}° / {round(day['temperature_2m_max'])}°" if day else '预报过期'
            label=CODES.get(snapshot['current']['weather_code'],'未知天气')
        else:
            label='暂无有效天气';limits='— / —'
        fit(c,label,371,217,17,150,fill=72,anchor='right')
        fit(c,limits,371,250,14,150,fill=92,anchor='right')
        c.text(lunar_label(now),20,282,11,85)
        c.text(freshness,381,283,10,100,anchor='right')
        return finish(c)
    if page != 'details':
        raise ValueError('页面只能为 home 或 details')
    fit(c,city+' · 天气总览',16,12,16,210,fill=45)
    draw_battery(c,status)
    c.text('数据 '+timestamp(snapshot['observed_at']).strftime('%H:%M') if snapshot else '等待更新',312,18,10,95,anchor='right')
    def section(box, title):
        c.rounded(box,9,None,190,0.75)
        from PIL import ImageFont
        from clock_page import font_path
        width=ImageFont.truetype(font_path('text'),10*SCALE).getlength(title)/SCALE
        c.rounded((23,box[1]-6,33+width,box[1]+6),2,255)
        c.text(title,28,box[1]-5,10,100)
    section((12,37,388,113),'此刻')
    section((12,125,388,194),'未来 6 小时 · 降水概率')
    section((12,206,388,279),'未来七天 · 上高 / 下低')
    if not snapshot:
        for y in (66,152,235):
            c.text('暂无有效天气',200,y,15,115,anchor='center')
    else:
        v=snapshot['current']
        weather_art(c,v['weather_code'],v['is_day'],(22,43,93,61))
        fit(c,f"{round(v['temperature_2m'])}°",126,49,36,92,fill=65,digits=True)
        fit(c,CODES.get(v['weather_code'],'未知天气'),126,91,12,94,fill=85)
        c.text('体感 '+str(round(v['apparent_temperature']))+'°',234,52,13,70)
        c.text(f"湿度 {v['relative_humidity_2m']:g}%",375,53,12,85,anchor='right')
        fit(c,f"风 {v['wind_speed_10m']:g} km/h",234,83,12,78,fill=90)
        fit(c,f"雨 {v['precipitation']:g} mm",375,83,12,62,fill=90,anchor='right')
        # Start at the next complete hour; current conditions have their own section.
        hours=[r for r in snapshot['hourly'] if timestamp(r['time'])>now][:6]
        for i in range(6):
            x=43+i*62.5
            if i:
                c.line([(x-31.25,140),(x-31.25,183)],225,0.5)
            if i>=len(hours):
                c.text('—',x,159,16,130,anchor='center');continue
            row=hours[i]
            c.text(timestamp(row['time']).strftime('%H时'),x,139,11,95,anchor='center')
            c.text(f"{round(row['temperature_2m'])}°",x,155,20,65,digits=True,anchor='center')
            c.text(f"{row['precipitation_probability']:g}%",x,180,10,115,digits=True,anchor='center')
        days=[r for r in snapshot['daily'] if r['time']>=now.date().isoformat()][:7]
        for i in range(7):
            x=39+i*(322/6)
            if i:
                c.line([(x-26.8,219),(x-26.8,266)],231,0.5)
            if i>=len(days):
                c.text('—',x,244,15,130,anchor='center');continue
            row=days[i];date=datetime.fromisoformat(row['time'])
            label='今天' if date.date()==now.date() else '周'+'一二三四五六日'[date.weekday()]
            c.text(label,x,217,11,80,anchor='center')
            name=art_name(row['weather_code'],True)
            if name:
                weather_art(c,row['weather_code'],True,(x-16,231,32,19))
            else:
                c.text('?',x,233,12,120,anchor='center')
            c.text(f"{round(row['temperature_2m_max'])}°",x,251,13,55,digits=True,anchor='center')
            c.text(f"{round(row['temperature_2m_min'])}°",x,267,10,115,digits=True,anchor='center')
    c.text('Open-Meteo · 模型天气',16,289,9,115)
    c.text(freshness,384,289,9,100,anchor='right')
    return finish(c)
