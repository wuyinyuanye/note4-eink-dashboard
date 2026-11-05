"""Open-Meteo model weather; validated snapshots, bounded stale cache, no invented values."""
from __future__ import annotations
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
from urllib.parse import urlencode
import subprocess
from clock_page import TZ

CURRENT = 'temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m'
HOURLY = 'temperature_2m,precipitation_probability,weather_code'
DAILY = 'weather_code,temperature_2m_max,temperature_2m_min,sunrise,sunset,precipitation_probability_max'
CODES = {0:'晴',1:'晴间多云',2:'多云',3:'阴',45:'雾',48:'雾凇',51:'小毛毛雨',53:'毛毛雨',55:'强毛毛雨',56:'冻毛毛雨',57:'强冻毛毛雨',61:'小雨',63:'中雨',65:'大雨',66:'冻雨',67:'强冻雨',71:'小雪',73:'中雪',75:'大雪',77:'米雪',80:'阵雨',81:'较强阵雨',82:'强阵雨',85:'阵雪',86:'强阵雪',95:'雷雨',96:'雷雨冰雹',99:'强雷雨冰雹'}


def timestamp(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        result = result.replace(tzinfo=TZ)
    return result.astimezone(TZ)


def request_json(url):
    # Use the host's TLS/network stack (curl), including its existing proxy setup.
    response = subprocess.run(['curl','--fail','--silent','--show-error',
                               '--connect-timeout','5','--max-time','20','--retry','1','--retry-delay','1',url],
                              capture_output=True,check=True,timeout=45)
    return json.loads(response.stdout)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('天气数值缺失或无效')
    return value


def parse(raw, city, latitude, longitude, now):
    current = raw['current']
    observed = timestamp(current['time'])
    if not -timedelta(minutes=30) <= now-observed <= timedelta(hours=3):
        raise ValueError('天气源时间过旧或超前')
    if raw.get('current_units',{}).get('temperature_2m') != '°C':
        raise ValueError('天气温度单位不是摄氏度')
    result = {'city':city,'latitude':latitude,'longitude':longitude,'source':'Open-Meteo',
              'observed_at':observed.isoformat(),'fetched_at':now.isoformat(),
              'current':{k:number(current[k]) for k in CURRENT.split(',')},'hourly':[],'daily':[]}
    if result['current']['is_day'] not in (0,1):
        raise ValueError('昼夜值无效')
    if not -100 <= result['current']['temperature_2m'] <= 70:
        raise ValueError('气温超出有效范围')
    for group, fields in [('hourly', HOURLY), ('daily', DAILY)]:
        block=raw[group]
        for i,t in enumerate(block['time']):
            entry={'time':t}
            for k in fields.split(','):
                value=block[k][i]
                entry[k]=value if k in ('sunrise','sunset') else number(value)
            result[group].append(entry)
    if not result['hourly'] or not result['daily']:
        raise ValueError('预报为空')
    return result


def geocode_city(query):
    """Look up a weather location without accepting arbitrary remote URLs."""
    if not isinstance(query,str) or not 1<=len(query.strip())<=40:
        raise ValueError('请输入1–40个字符的地点名称')
    params=urlencode({'name':query.strip(),'count':8,'language':'zh','format':'json'})
    try:
        raw=request_json('https://geocoding-api.open-meteo.com/v1/search?'+params)
        rows=raw.get('results') or []
    except Exception as exc:
        raise ValueError('地点查询服务暂时不可用，可直接填写经纬度') from exc
    results=[]
    for row in rows[:8]:
        if not isinstance(row,dict): continue
        name=row.get('name');country=row.get('country');admin=row.get('admin1')
        latitude=row.get('latitude');longitude=row.get('longitude')
        if not isinstance(name,str) or not 1<=len(name)<=80: continue
        if isinstance(latitude,bool) or isinstance(longitude,bool): continue
        if not isinstance(latitude,(int,float)) or not isinstance(longitude,(int,float)): continue
        if not math.isfinite(latitude) or not math.isfinite(longitude) or not -90<=latitude<=90 or not -180<=longitude<=180:
            continue
        parts=[]
        for part in (name,admin,country):
            if isinstance(part,str) and part.strip() and part.strip() not in parts:
                parts.append(part.strip()[:80])
        results.append({'name':name.strip(),'label':' · '.join(parts),
                        'latitude':round(float(latitude),6),'longitude':round(float(longitude),6),
                        'country_code':row.get('country_code') if isinstance(row.get('country_code'),str) else '',
                        'timezone':row.get('timezone') if isinstance(row.get('timezone'),str) else ''})
    if not results:
        raise ValueError('没有找到这个地点，请换一个名称或直接填写经纬度')
    return results


class WeatherStore:
    def __init__(self, config, directory: Path, fetch=request_json):
        self.config=config
        self.path=directory/'weather-cache.json'
        self.fetch=fetch
        self.snapshot=None
        self.error=None
        try:
            saved=json.loads(self.path.read_text())
            if all(saved.get(k)==config[k] for k in ('city','latitude','longitude')):
                # Revalidate cached structure, not just its timestamp.
                for k in CURRENT.split(','): number(saved['current'][k])
                timestamp(saved['observed_at']); timestamp(saved['fetched_at'])
                for group, fields in (('hourly',HOURLY),('daily',DAILY)):
                    if not saved[group]: raise ValueError('空预报')
                    for entry in saved[group]:
                        timestamp(entry['time'])
                        for key in fields.split(','):
                            if key not in ('sunrise','sunset'): number(entry[key])
                self.snapshot=saved
        except (OSError,ValueError,KeyError,TypeError): pass

    def update(self, now=None):
        now=now or datetime.now(TZ)
        params={k:self.config[k] for k in ('latitude','longitude')}
        params.update(current=CURRENT,hourly=HOURLY,daily=DAILY,timezone='Asia/Shanghai',forecast_days=7,wind_speed_unit='kmh')
        try:
            value=parse(self.fetch('https://api.open-meteo.com/v1/forecast?'+urlencode(params, safe=',')),
                        self.config['city'],self.config['latitude'],self.config['longitude'],now)
            self.path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.path.with_suffix('.tmp')
            temporary.write_text(json.dumps(value,ensure_ascii=False)); temporary.replace(self.path)
            self.snapshot=value; self.error=None
            return True
        except Exception as exc:
            self.error=f'{type(exc).__name__}: 天气更新失败'
            return False

    def view(self, now=None):
        now=now or datetime.now(TZ)
        if not self.snapshot: return None,'天气未连接'
        age=now-timestamp(self.snapshot['observed_at'])
        if age > timedelta(hours=6) or age < -timedelta(minutes=30): return None,'天气已过期'
        stale=bool(self.error) or age > timedelta(minutes=90)
        label=('缓存 '+timestamp(self.snapshot['observed_at']).strftime('%H:%M') if stale else
               '获取 '+timestamp(self.snapshot['fetched_at']).strftime('%H:%M:%S'))
        return self.snapshot,label
