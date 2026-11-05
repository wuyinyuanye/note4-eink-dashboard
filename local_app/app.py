"""NOTE4 local application: pages, scheduling, and persistent SDK session."""
from __future__ import annotations
import argparse
import concurrent.futures
import io
import ipaddress
import json
import math
import os
import queue
import re
import signal
import socket
import struct
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote
from clock_page import HERE, TZ
from weather import WeatherStore, geocode_city
from weather_page import render
from album import Album, MAX_UPLOAD, is_landscape_image, local_photo_bytes, local_photo_candidates, remote_photos
from market import Market, fetch_quotes, normalize_codes, startup_quotes_current
from market_page import render_market
from news import News, fetch_news
from news_page import render_news
from menu_page import APPS, render_menu
from gold import Gold
from gold_feed import fetch_quote as fetch_gold_quote
from gold_references import fetch_reference as fetch_gold_reference
from gold_page import render_gold
from portfolio import Portfolio, fetch_quote as fetch_portfolio_quote
from portfolio_page import render_portfolio
from sectors import Sectors, CODES as SECTOR_CODES
from sectors_page import render_sectors

DEFAULTS={'city':'上海','latitude':31.2304,'longitude':121.4737,
          'target':'192.0.2.100','expected_id':'0123456789ab','weather_interval_seconds':3600}
DATA=Path(os.environ.get('NOTE4_DATA_DIR',Path.home()/'Library/Application Support/ZectrixNote4/weather-app'))
PAGES=('home','details')
BRIDGE=Path(os.environ.get('NOTE4_BRIDGE',HERE/'bridge/target/debug/note4-weather-bridge'))


def validate_city(value):
    if not isinstance(value,str):
        raise ValueError('城市名称必须是文字')
    value=value.strip()
    if not 1<=len(value)<=40 or any(ord(char)<32 or ord(char)==127 for char in value):
        raise ValueError('城市名称应为1–40个可见字符')
    return value


def validate_coordinate(value,label,low,high):
    if isinstance(value,bool):
        raise ValueError(f'{label}无效')
    try: number=float(value)
    except (TypeError,ValueError): raise ValueError(f'{label}必须是数字') from None
    if not math.isfinite(number) or not low<=number<=high:
        raise ValueError(f'{label}应在{low:g}到{high:g}之间')
    return round(number,6)


def validate_weather_settings(value):
    if not isinstance(value,dict): raise ValueError('天气设置无效')
    return {'city':validate_city(value.get('city')),
            'latitude':validate_coordinate(value.get('latitude'),'纬度',-90,90),
            'longitude':validate_coordinate(value.get('longitude'),'经度',-180,180)}


def validate_device_settings(value):
    if not isinstance(value,dict): raise ValueError('设备设置无效')
    target=value.get('target')
    expected_id=value.get('expected_id')
    if not isinstance(target,str): raise ValueError('设备地址必须是文字')
    target=target.strip()
    match=re.fullmatch(r'(\d{1,3}(?:\.\d{1,3}){3})(?::(\d{1,5}))?',target)
    if not match: raise ValueError('设备地址应为 IPv4，例如 192.0.2.100:32342')
    try: host=str(ipaddress.IPv4Address(match.group(1)))
    except ipaddress.AddressValueError: raise ValueError('设备 IPv4 地址无效') from None
    port=int(match.group(2) or 32342)
    if not 1<=port<=65535: raise ValueError('设备端口应在1–65535之间')
    if not isinstance(expected_id,str) or not re.fullmatch(r'[0-9a-fA-F]{8,64}',expected_id.strip()):
        raise ValueError('设备 ID 应为8–64位十六进制字符')
    return {'target':f'{host}:{port}' if port!=32342 else host,
            'expected_id':expected_id.strip().lower()}


def save_config(path,config):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    try: temporary.chmod(0o600)
    except OSError: pass
    temporary.replace(path)


def save_bounded_stderr(stream, path, limit=65536):
    """Drain the child continuously into RAM, saving a bounded tail only on exit."""
    tail=''
    try:
        while True:
            chunk=stream.read(4096)
            if not chunk: break
            tail=(tail+chunk)[-limit:]
        if tail: path.write_text(tail)
    except (OSError,ValueError):
        pass


def append_display_history(path, receipt, limit=262144):
    line=(json.dumps(receipt,ensure_ascii=False)+'\n').encode('utf-8')
    if len(line)>limit: raise ValueError('显示记录过大')
    size=path.stat().st_size if path.exists() else 0
    if size+len(line)>limit:
        retained=min(limit//2,limit-len(line))
        with path.open('rb') as source:
            offset=max(0,size-retained)
            source.seek(offset);tail=source.read(retained)
        if offset: tail=tail.partition(b'\n')[2]
        temporary=path.with_suffix('.tmp');temporary.write_bytes(tail+line);temporary.replace(path)
    else:
        with path.open('ab') as history: history.write(line)


def probe(target, expected_id):
    """Read-only INFO, verify identity/canvas BEFORE taking device ownership."""
    host,_,port=target.partition(':')
    socket.inet_aton(host)
    with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as sock:
        sock.settimeout(3);sock.connect((host,int(port or 32342)))
        sock.send(b'ZL\x01\x01'+struct.pack('>QI',0,1))
        packet=sock.recv(1200)
    if len(packet)<20 or packet[:4]!=b'ZL\x01\x81' or packet[12:16]!=struct.pack('>I',1):
        raise ValueError('设备 INFO 响应无效')
    b=packet[16:]
    if b[0]!=0: raise ValueError('设备 INFO 请求失败')
    n=b[2];device_id=b[3:3+n].decode();m=b[3+n]
    dimensions=b[4+n+m:]
    if device_id.lower()!=expected_id.lower(): raise ValueError('设备身份不匹配，取消连接')
    if dimensions!=bytes([1,144,1,44,0,0,0,0,0,0,0,0]): raise ValueError('设备不支持完整画布')
    if b[1]: raise ValueError('设备正在其他会话中')
    return device_id


class App:
    def __init__(self, config, directory=DATA, config_path=None):
        if config.get('market_image_format','Gray4') not in ('Gray4','Mono'):
            raise ValueError('行情格式必须为Gray4或Mono')
        self.config=config;self.directory=directory;directory.mkdir(parents=True,exist_ok=True)
        self.config_path=Path(config_path) if config_path else directory/'config.json'
        self.store=WeatherStore(config,directory)
        self.album=Album(directory,os.environ.get('NOTE4_PHOTO_INDEX'),os.environ.get('NOTE4_PHOTO_ROOT'))
        self.album_syncing=False;self.album_error=None;self.album_daily_retry_at=0;self.last_slide=time.monotonic()
        self.market=Market(directory)
        self.sectors=Sectors(directory)
        self.news=News(directory)
        self.gold=Gold(directory)
        self.portfolio=Portfolio(directory)
        self.menu_index=0;self.menu_return_page=None;self.market_candidates={}
        self.events=queue.Queue(maxsize=64)
        self.lock=threading.RLock();self.stop=threading.Event()
        self.page='home';self.process=None
        self.connection='未连接';self.error=None;self.palette=None;self.palette4=None;self.device=None;self.telemetry=None
        self.recording=False;self.blocked=False;self.inflight=None;self.pending=False
        self.generation=0;self.displayed=None;self.display_count=0;self.displayed_page=None
        self.last_render=None;self.last_fetch=0;self.last_status=0;self.fetching=False;self.initial_connect=False
        self.startup_page=None;self.startup_pending=None;self.startup_attempts=0;self.startup_retry_at=0
        self.preview=b'';self.preview_format=None;self.displayed_format=None;self.last_event=None;self.revision=0
        self.last_key=None
        self.reconnect_enabled=False;self.reconnect_attempts=0;self.reconnect_at=None;self.reconnect_after_exit=False
        self.awaiting_connect_image=False
        self.load_telemetry()
        self.battery_resolved = bool(self.telemetry and type(self.telemetry.get('battery_percent')) is int and 0 <= self.telemetry['battery_percent'] <= 100)
        # Preserve the hourly fetch cadence across local app restarts.
        if self.store.snapshot:
            try:
                age=(datetime.now(TZ)-datetime.fromisoformat(self.store.snapshot['fetched_at'])).total_seconds()
                if 0<=age<self.config['weather_interval_seconds']:
                    self.last_fetch=time.monotonic()-age
            except (ValueError,TypeError,KeyError): pass
        self.render()

    def image_format(self):
        if self.page=='menu': return 'Mono'
        if self.page=='sectors': return 'Mono'
        return self.config.get('market_image_format','Gray4') if self.page=='market' else 'Gray16'

    def load_telemetry(self):
        try:
            saved=json.loads((self.directory/'device-status.json').read_text())
            recorded=datetime.fromisoformat(saved['recorded_at'])
            age=(datetime.now(TZ)-recorded).total_seconds()
            status=saved['status'];percent=status.get('battery_percent')
            valid=percent is None or (type(percent) is int and 0<=percent<=100)
            if saved.get('expected_id')==self.config['expected_id'] and -30<=age<=7200 and valid:
                self.telemetry=status
        except (OSError,ValueError,KeyError,TypeError,AttributeError): pass

    def render(self, now=None, force=False, reason='page'):

        now=now or datetime.now(TZ)
        snapshot,freshness=self.store.view(now)
        image=(render_menu(self.menu_index,mono=True) if self.page=='menu' else render_sectors(self.sectors) if self.page=='sectors' else render_news(self.news,now) if self.page=='news' else render_gold(self.gold.quote,self.gold.references) if self.page=='gold' else render_portfolio(self.portfolio) if self.page=='portfolio' else render_market(self.market,now,mono=self.image_format()=='Mono') if self.page=='market' else self.album.render() if self.page=='album' else
               render(now,snapshot,freshness,self.page,self.config['city'],home_frame=self.config.get('home_frame',True),status=self.telemetry))
        output=io.BytesIO();image.save(output,format='PNG');png=output.getvalue()
        self.last_render=now.strftime('%Y-%m-%dT%H:%M')
        image_format=self.image_format()
        if png==self.preview and image_format==self.preview_format and not force: return
        self.preview=png;self.preview_format=image_format;self.revision+=1;self.pending_reason=reason
        temporary=self.directory/'preview.tmp';temporary.write_bytes(self.preview);temporary.replace(self.directory/'preview.png')
        self.pending=True;self.dispatch()

    def tick(self, now=None, monotonic_now=None):
        now=now or datetime.now(TZ)
        monotonic_now=time.monotonic() if monotonic_now is None else monotonic_now
        if self.reconnect_at is not None and monotonic_now>=self.reconnect_at:
            self.reconnect_at=None
            if self.reconnect_enabled and not self.blocked and self.page!='gold': self.connect()
        if self.initial_connect and self.startup_pending is None and monotonic_now>=self.startup_retry_at:
            self.startup_attempt()
        if (self.album.needs_daily(now.date()) and not self.album_syncing
                and monotonic_now>=self.album_daily_retry_at):
            self.album_daily_retry_at=monotonic_now+900
            try: self.sync_album()
            except ValueError as exc: self.album_error=str(exc)
        if monotonic_now-self.last_fetch>=self.config['weather_interval_seconds']: self.fetch()
        if self.page=='album' and self.album.playing and len(self.album.photos)>1 and monotonic_now-self.last_slide>=self.album.interval:
            if self.connection=='已连接' and not self.recording and not self.blocked and self.inflight is None:
                self.album.move(1);self.last_slide=monotonic_now;self.render(reason='album_slideshow')
        if self.page=='market':
            if self.market.due(now): self.fetch_market(now)
        if self.page=='sectors' and self.sectors.due(now): self.fetch_sectors(now)
        if self.page=='news' and self.news.due(now): self.fetch_news(now)
        if self.page=='portfolio' and self.portfolio.due(now): self.fetch_portfolio(now)
        if self.page in PAGES and self.last_render and now.date().isoformat()!=self.last_render[:10]:
            self.render(now=now,reason='date')

    def send(self, value):
        if self.process and self.process.poll() is None:
            try:
                self.process.stdin.write(json.dumps(value)+'\n');self.process.stdin.flush()
            except (BrokenPipeError,OSError): self.connection='连接已结束';self.pending=False

    def dispatch(self):
        if self.connection!='已连接' or self.inflight is not None or self.recording or self.blocked or not self.pending: return
        self.generation+=1
        path=self.directory/'inflight.png';path.write_bytes(self.preview)
        self.inflight=self.generation;self.inflight_page=self.page;self.inflight_reason=self.pending_reason;self.pending=False
        self.inflight_format=self.preview_format
        self.send({'command':'show','id':self.inflight,'path':str(path),'format':self.inflight_format})

    def fetch(self):
        if self.fetching: return
        self.fetching=True;self.last_fetch=time.monotonic()
        def worker():
            # Store is only swapped on the application loop.
            store=WeatherStore(self.config,self.directory)
            success=store.update()
            self.events.put(('weather',(store,success)))
        threading.Thread(target=worker,daemon=True).start()

    def weather_settings(self, value):
        if self.fetching:
            raise ValueError('天气正在更新，请稍后保存地点')
        settings=validate_weather_settings(value)
        updated={**self.config,**settings}
        save_config(self.config_path,updated)
        self.config=updated
        self.store=WeatherStore(self.config,self.directory)
        self.last_fetch=0
        self.error=None
        self.fetch()

    def device_settings(self, value):
        if self.process and self.process.poll() is None or self.connection in ('已连接','正在连接','正在关闭'):
            raise ValueError('请先断开设备，再修改设备地址或 ID')
        settings=validate_device_settings(value)
        updated={**self.config,**settings}
        save_config(self.config_path,updated)
        self.config=updated
        self.device=None;self.telemetry=None;self.battery_resolved=False
        self.reconnect_enabled=False;self.reconnect_at=None

    def schedule_reconnect(self):
        if not self.reconnect_enabled or self.blocked or self.page=='gold' or self.initial_connect: return
        if self.reconnect_attempts>=3:
            self.connection='连接失败';self.error='自动重连未成功，请检查设备网络后手动连接';return
        delay=(15,30,60)[self.reconnect_attempts];self.reconnect_attempts+=1
        self.reconnect_at=time.monotonic()+delay;self.connection='等待自动重连'
        self.error=f'设备连接中断，将于{delay}秒后重试（{self.reconnect_attempts}/3）'

    def connect(self):
        if self.process and self.process.poll() is None: return
        self.reconnect_enabled=True;self.reconnect_at=None
        self.connection='正在连接';self.error=None;self.blocked=False;self.recording=False;self.inflight=None
        try:
            probe(self.config['target'],self.config['expected_id'])
            initial=self.directory/'initial.png';initial.write_bytes(self.preview)
            self.process=subprocess.Popen([str(BRIDGE),self.config['target'],self.config['expected_id'],str(initial),self.preview_format],
                                          stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,bufsize=1)
            process=self.process
            threading.Thread(target=save_bounded_stderr,args=(process.stderr,self.directory/'bridge.stderr.log'),daemon=True).start()
            self.awaiting_connect_image=True;self.pending=False;self.inflight_format=self.preview_format
            def reader():
                for line in process.stdout:
                    try: self.events.put(('bridge',json.loads(line)))
                    except ValueError: pass
                code=process.wait();self.events.put(('ended',(process,code)))
            threading.Thread(target=reader,daemon=True).start()
        except Exception as exc:
            self.connection='连接失败';self.error=('设备未响应，请唤醒设备并开启“待机保持 Wi-Fi”' if isinstance(exc,TimeoutError) else str(exc))
            if isinstance(exc,TimeoutError): self.schedule_reconnect()

    def disconnect(self):
        self.send({'command':'close'})
        self.connection='正在关闭';self.pending=False;self.initial_connect=False
        self.reconnect_enabled=False;self.reconnect_at=None;self.reconnect_after_exit=False

    def request_startup_connect(self):
        self.initial_connect=True;self.startup_page=self.page;self.startup_pending=None
        self.startup_attempts=0;self.startup_retry_at=0;self.connection='等待最新数据'

    def startup_attempt(self):
        if not self.initial_connect: return
        if self.page!=self.startup_page:
            self.initial_connect=False;self.connection='未连接';return
        self.startup_attempts+=1
        if self.page in PAGES:
            self.startup_pending='weather';self.fetch()
        elif self.page=='market':
            self.startup_pending='market';self.fetch_market(trigger='startup')
        elif self.page=='sectors':
            self.startup_pending='sectors';self.fetch_sectors()
        elif self.page=='news':
            self.startup_pending='news';self.fetch_news(trigger='startup')
        elif self.page=='portfolio' and self.portfolio.config:
            self.startup_pending='portfolio';self.fetch_portfolio()
        elif self.page=='album' and self.album.photos:
            self.startup_pending='album';self.startup_result('album',True)
        else:
            self.initial_connect=False;self.connection='未连接'
            self.error='当前页面不自动更新，请手动选择并连接设备'

    def startup_result(self, kind, success):
        if not self.initial_connect or self.startup_pending!=kind: return
        self.startup_pending=None
        if self.page!=self.startup_page:
            self.initial_connect=False;self.connection='未连接';return
        if success:
            self.connect()
            if self.connection!='连接失败':
                self.initial_connect=False;return
        if self.startup_attempts>=3:
            self.initial_connect=False;self.connection='连接失败'
            self.error='自动恢复失败，请检查数据和设备后手动重试';return
        self.connection='等待自动重试';self.error='恢复未完成，5分钟后重试'
        self.startup_retry_at=time.monotonic()+300

    def action(self, action):
        previous_page=self.page
        if self.initial_connect and action not in ('disconnect',):
            self.initial_connect=False;self.startup_pending=None
            if self.connection in ('等待最新数据','等待自动重试'): self.connection='未连接'
        if self.page=='menu' and action in (*PAGES,'album','market','sectors','news','gold','portfolio','album_play','album_pause'):
            self.menu_return_page=None
        if action in ('album','market','sectors','news','gold','portfolio'):
            self.menu_index=next(i for i,item in enumerate(APPS) if item[0]==action)
        elif action in PAGES:
            self.menu_index=next(i for i,item in enumerate(APPS) if item[0]=='home')
        if action=='menu':
            if self.page=='menu':
                self.return_from_menu()
            else:
                self.menu_return_page=self.page
                current='home' if self.page in PAGES else self.page
                self.menu_index=next((i for i,item in enumerate(APPS) if item[0]==current),0)
                self.page='menu';self.render(reason='menu_open')
        elif self.page=='menu' and action in ('up','down','confirm'):
            if action=='confirm':
                target=APPS[self.menu_index][0]
                original='home' if self.menu_return_page in PAGES else self.menu_return_page
                if target==original:
                    self.return_from_menu()
                else:
                    self.menu_return_page=None
                    self.action(target)
            else:
                self.menu_index=(self.menu_index+(-1 if action=='up' else 1))%len(APPS)
                self.render(reason='menu_navigation')
        elif action=='market':
            self.page='market';self.render(reason='market_open')
            now=datetime.now(TZ)
            age=(now-datetime.fromisoformat(self.market.fetched_at)).total_seconds() if self.market.fetched_at else float('inf')
            if age>=self.market.interval: self.fetch_market(now)
        elif action=='market_refresh': self.fetch_market(trigger='manual')
        elif self.page=='market' and action in ('up','down','confirm'):
            if action=='confirm': self.fetch_market(trigger='confirm')
            else:
                pages=(len(self.market.codes)+5)//6
                self.market.page=(self.market.page+(-1 if action=='up' else 1))%pages
                self.render(reason='market_navigation')
        elif action=='sectors':
            self.page='sectors';self.render(reason='sectors_open')
            now=datetime.now(TZ)
            age=(now-datetime.fromisoformat(self.sectors.fetched_at)).total_seconds() if self.sectors.fetched_at else float('inf')
            if age>=self.sectors.interval: self.fetch_sectors(now)
        elif action=='sectors_refresh': self.fetch_sectors()
        elif self.page=='sectors' and action in ('up','down','confirm'):
            if action=='confirm': self.fetch_sectors()
        elif action=='news':
            self.page='news';self.render(reason='news_open')
            if self.news.due(datetime.now(TZ)): self.fetch_news(trigger='open')
        elif action=='news_refresh': self.fetch_news(trigger='manual')
        elif self.page=='news' and action in ('up','down','confirm'):
            if action=='confirm': self.fetch_news(trigger='confirm')
        elif action=='gold':
            self.page='gold';self.render(reason='gold_open')
            if self.gold.quote is None: self.fetch_gold()
        elif action=='gold_refresh': self.fetch_gold()
        elif self.page=='gold' and action in ('up','down','confirm'):
            if action=='confirm': self.fetch_gold()
        elif action=='portfolio':
            self.page='portfolio';self.render(reason='portfolio_open')
            if self.portfolio.due(datetime.now(TZ)): self.fetch_portfolio()
        elif action=='portfolio_refresh': self.fetch_portfolio()
        elif self.page=='portfolio' and action in ('up','down','confirm'):
            if action=='confirm': self.fetch_portfolio()
        elif action in ('album_play','album_pause'):
            self.page='album';self.album.playing=action=='album_play' and bool(self.album.photos)
            self.album.save();self.last_slide=time.monotonic();self.render(reason='album_open')
        elif action=='album':
            self.page='album';self.last_slide=time.monotonic();self.render(reason='album_open')
        elif self.page=='album' and action in ('up','down','confirm'):
            if action=='confirm': self.album.playing=not self.album.playing and bool(self.album.photos);self.album.save()
            else: self.album.move(-1 if action=='up' else 1);self.render(reason='album_navigation')
            self.last_slide=time.monotonic()
        elif action in PAGES:
            self.page=action;self.render()
        elif action in ('up','down'):
            self.page=PAGES[(PAGES.index(self.page)+(-1 if action=='up' else 1))%len(PAGES)];self.render()
        elif action=='confirm':
            self.fetch()
        elif action=='refresh': self.fetch()
        elif action=='connect':
            self.reconnect_attempts=0;self.connect()
        elif action=='disconnect': self.disconnect()
        elif action=='retry_display':
            self.blocked=False;self.error=None;self.render(force=True)
        if self.page != previous_page and self.page!='menu': self.remember_page()

    def remember_page(self):
        if self.page not in (*PAGES,'album','market','sectors','news','gold','portfolio'): return
        try:
            path=self.directory/'current-page.json';temporary=path.with_suffix('.tmp')
            temporary.write_text(json.dumps({'page':self.page},ensure_ascii=False))
            temporary.replace(path)
        except OSError:
            self.error='无法保存当前页面，重启后可能返回天气'

    def restore_page(self):
        try:
            page=json.loads((self.directory/'current-page.json').read_text())['page']
            if page not in (*PAGES,'album','market','sectors','news','gold','portfolio'): raise ValueError('页面无效')
            return page
        except FileNotFoundError:
            return 'home'
        except (OSError,ValueError,KeyError,TypeError):
            self.error='保存的页面无效，已返回天气'
            return 'home'

    def return_from_menu(self):
        self.page=self.menu_return_page or 'home'
        self.menu_return_page=None
        self.last_slide=time.monotonic()
        self.render(reason='menu_return')

    def fetch_market(self, now=None, trigger='auto'):
        if self.market.fetching: return
        now=now or datetime.now(TZ);self.market.started(now);codes=self.market.codes.copy()
        self.market.last_refresh_trigger=trigger
        def worker():
            try:
                rows=fetch_quotes(codes)
                value=(rows,datetime.now(TZ),None)
            except Exception:
                value=(None,datetime.now(TZ),'行情获取失败，请检查网络或代码后重试')
            self.events.put(('market',value))
        threading.Thread(target=worker,daemon=True).start()

    def market_result(self, value):
        self.market.accept(*value)
        if self.page=='market': self.render(reason='market')
        self.startup_result('market',self.market.error is None and
                            startup_quotes_current(self.market.rows,self.market.codes,datetime.now(TZ)))

    def fetch_sectors(self, now=None):
        if self.sectors.fetching: return
        self.sectors.started(now or datetime.now(TZ))
        def worker():
            try: value=(fetch_quotes(SECTOR_CODES),datetime.now(TZ),None)
            except Exception: value=(None,datetime.now(TZ),'板块行情获取失败，保留上次画面')
            self.events.put(('sectors',value))
        threading.Thread(target=worker,daemon=True).start()

    def sectors_result(self, value):
        accepted=self.sectors.accept(*value)
        if accepted and self.page=='sectors': self.render(reason='sectors')
        self.startup_result('sectors',accepted)

    def fetch_news(self, now=None, trigger='auto'):
        if self.news.fetching: return
        now=now or datetime.now(TZ);self.news.started(now,trigger)
        def worker():
            try: value=(fetch_news(),datetime.now(TZ),None)
            except Exception: value=(None,datetime.now(TZ),'新闻获取失败，请检查网络后重试')
            self.events.put(('news',value))
        threading.Thread(target=worker,daemon=True).start()

    def news_result(self, value):
        accepted=self.news.accept(*value)
        if accepted and self.page=='news': self.render(reason='news')
        self.startup_result('news',accepted)

    def fetch_gold(self):
        if self.gold.fetching: return
        self.gold.started(datetime.now(TZ))
        def worker():
            results={}
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                jobs={'quote':pool.submit(fetch_gold_quote),
                      'comex':pool.submit(fetch_gold_reference,'comex'),
                      'etf':pool.submit(fetch_gold_reference,'etf')}
                for key, job in jobs.items():
                    try: results[key]=job.result()
                    except Exception: results[key]=None
            self.events.put(('gold',results))
        threading.Thread(target=worker,daemon=True).start()

    def gold_result(self, value):
        accepted=self.gold.accept(value)
        if accepted and self.page=='gold': self.render(reason='gold')

    def fetch_portfolio(self, now=None):
        if self.portfolio.fetching or not self.portfolio.config: return
        self.portfolio.started(now or datetime.now(TZ));code=self.portfolio.config['code']
        def worker():
            try: value=(fetch_portfolio_quote(code),datetime.now(TZ),None)
            except Exception: value=(None,datetime.now(TZ),'持仓行情获取失败')
            self.events.put(('portfolio',value))
        threading.Thread(target=worker,daemon=True).start()

    def portfolio_result(self, value):
        accepted=self.portfolio.accept(*value)
        if accepted and self.page=='portfolio': self.render(reason='portfolio')
        self.startup_result('portfolio',accepted)

    def sync_album(self):
        if self.album_syncing: return
        local=self.album.local_library_available
        if not local and not self.album.source: raise ValueError('照片索引不可用，请检查只读目录或服务地址')
        selected_date=datetime.now(TZ).date();self.album_syncing=True;self.album_error=None
        self.album_daily_retry_at=time.monotonic()+900
        source=self.album.source;offset=self.album.remote_offset
        def worker():
            error=None
            try:
                if local:
                    candidates=local_photo_candidates(
                        self.album.photo_index,self.album.photo_root,
                        exclude_ids=self.album.daily_used_ids,
                        seed=f'{selected_date.isoformat()}:{self.album.photo_index}')
                    if not candidates: raise ValueError('照片目录中没有可用的横屏照片')
                    for photo in candidates[:12]:
                        data,name=local_photo_bytes(self.album.photo_root,photo)
                        if not is_landscape_image(data): continue
                        photo['name']=name;photo['source']='照片目录';photo['_daily_date']=selected_date.isoformat()
                        self.events.put(('album_photo',(source,data,photo)));break
                    else: raise ValueError('没有找到可解码的横屏照片')
                else:
                    for data,photo in remote_photos(source,limit=24,offset=offset):
                        if not is_landscape_image(data): continue
                        photo['_daily_date']=selected_date.isoformat()
                        self.events.put(('album_photo',(source,data,photo)));break
                    else: raise ValueError('相框服务返回的照片中没有可用的横屏照片')
            except Exception as exc: error=f'照片同步失败，保留已有缓存：{exc}'
            self.events.put(('album_done',(error,selected_date.isoformat())))
        threading.Thread(target=worker,daemon=True).start()

    def album_changed(self):
        self.last_slide=time.monotonic()
        if self.page=='album': self.render(reason='album_edit')

    def bridge_event(self, event):
        kind=event.get('event');self.last_event=kind
        if kind=='connected':
            self.connection='已连接';self.device=event['device'];self.palette=event['gray16'];self.palette4=event.get('gray4');self.last_status=0
            self.send({'command':'status'})
        elif kind=='displayed':
            self.displayed=datetime.now(TZ).isoformat();self.display_count+=1;self.displayed_page=self.inflight_page
            self.displayed_format=event.get('wire_format',getattr(self,'inflight_format','Gray16'))
            self.last_display_reason=getattr(self,'inflight_reason','unknown')
            receipt={'displayed_at':self.displayed,'page':self.displayed_page,'reason':getattr(self,'inflight_reason','unknown'),'target':self.config['target'],
                     'device_id':self.device['id'] if self.device else None,'format':self.displayed_format,
                     'gray16':self.palette,'gray4':self.palette4,'wire_format':self.displayed_format,
                     'bpp':{'Mono':1,'Gray4':2,'Gray16':4}[self.displayed_format],'meaning':'SDK reported display complete; visual acceptance pending'}
            (self.directory/'last-display.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
            append_display_history(self.directory/'display-history.jsonl',receipt)
            self.inflight=None;self.error=None;self.reconnect_attempts=0;self.reconnect_at=None;self.dispatch()
        elif kind=='display_error':
            self.inflight=None;self.error=event['error']
            # ResultUnknown/transport errors latch until an explicit user action.
            self.blocked=event.get('kind')!='Busy'
            if not self.blocked: self.pending=True
        elif kind=='key':
            self.last_key={'key':event.get('key'),'action':event.get('action'),'at':datetime.now(TZ).isoformat()}
            if event.get('action')=='Short': self.action({'Up':'up','Down':'down','Confirm':'confirm'}[event['key']])
            elif event.get('key') in ('Up','Down'): self.action('menu')
        elif kind=='recording_started': self.recording=True
        elif kind=='recording_finished':
            self.recording=False;self.dispatch()
        elif kind=='status':
            self.telemetry=event['status']
            percent=self.telemetry.get('battery_percent')
            if not self.battery_resolved and type(percent) is int and 0<=percent<=100:
                self.battery_resolved=True
            self.finish_connect_image()
            # Telemetry is transient; polling should not cause persistent writes.
        elif kind=='status_error':
            self.finish_connect_image()
        elif kind=='disconnected':
            reason=event.get('reason',event.get('error'))
            safe=(reason in ('Timeout','Network') and self.inflight is None and not self.blocked and not self.recording)
            self.connection='连接已结束';self.error=reason;self.blocked=not safe;self.inflight=None
            self.reconnect_after_exit=safe
            self.awaiting_connect_image=False
            self.send({'command':'close'})

    def finish_connect_image(self):
        if not self.awaiting_connect_image: return
        self.awaiting_connect_image=False
        if self.connection=='已连接': self.render(force=True,reason='connect')

    def bridge_ended(self, code):
        # Before 'connected' the bridge cannot have submitted the initial image.
        # After a timeout, only an idle, known-complete image may be replayed.
        safe_before_open=self.connection=='正在连接'
        safe_idle=(self.connection=='已连接' and self.inflight is None and not self.blocked and not self.recording)
        safe=self.reconnect_after_exit or safe_before_open or safe_idle
        uncertain=(not safe and self.inflight is not None)
        self.connection='已断开';self.inflight=None;self.pending=False;self.recording=False
        self.awaiting_connect_image=False
        self.reconnect_after_exit=False
        if code: self.error=f'SDK 进程退出（{code}），查看本地日志'
        if uncertain: self.blocked=True;self.error='显示结果不确定，请确认设备画面后手动处理'
        if safe: self.schedule_reconnect()

    def state(self):
        with self.lock:
            snapshot,freshness=self.store.view()
            menu={'index':self.menu_index,'title':APPS[self.menu_index][1],
                  'previous':APPS[(self.menu_index-1)%len(APPS)][1],
                  'next':APPS[(self.menu_index+1)%len(APPS)][1],'return_page':self.menu_return_page}
            return {'menu':menu,'news':self.news.state(),'gold':self.gold.state(),'portfolio':self.portfolio.state(),'market':self.market.state(datetime.now(TZ)),'sectors':self.sectors.state(datetime.now(TZ)),'album_syncing':self.album_syncing,'album_error':self.album_error,'album':self.album.state(),'city':self.config['city'],'latitude':self.config['latitude'],'longitude':self.config['longitude'],'page':self.page,'connection':self.connection,'target':self.config['target'],'expected_id':self.config['expected_id'],
                    'device':self.device,'last_key':self.last_key,'gray16':self.palette,'gray4':self.palette4,'image_format':self.preview_format,'displayed_format':self.displayed_format,'clock_visible':False,'status':self.telemetry,'weather':snapshot,
                    'freshness':freshness,'weather_error':self.store.error,'error':self.error,'recording':self.recording,
                    'display_blocked':self.blocked,'displayed_at':self.displayed,'display_count':self.display_count,
                    'displayed_page':self.displayed_page,'inflight':self.inflight,'fetching':self.fetching,'last_event':self.last_event,'revision':self.revision,'weather_interval_seconds':self.config['weather_interval_seconds'],'battery_refresh':'with_weather_or_navigation','last_display_reason':getattr(self,'last_display_reason',None)}

    def run(self):
        while not self.stop.is_set():
            try: kind,value=self.events.get(timeout=0.4)
            except queue.Empty: kind,value=None,None
            with self.lock:
                try:
                    if kind=='sectors': self.sectors_result(value)
                    elif kind=='market': self.market_result(value)
                    elif kind=='news': self.news_result(value)
                    elif kind=='gold': self.gold_result(value)
                    elif kind=='portfolio': self.portfolio_result(value)
                    elif kind=='album_photo':
                        source,data,photo=value
                        if source==self.album.source:
                            daily_date=photo.pop('_daily_date',None)
                            index=self.album.cache_remote(data,photo,landscape_only=bool(daily_date))
                            if daily_date:
                                self.album.complete_daily(datetime.fromisoformat(daily_date).date(),photo['id'],index,photo.get('source') or '照片目录')
                            self.album_error=None;self.album_daily_retry_at=0
                            if self.page=='album': self.album_changed()
                    elif kind=='album_done':
                        self.album_syncing=False
                        error,daily_date=value
                        if error:
                            self.album_error=error;self.album_daily_retry_at=time.monotonic()+900
                        elif self.album.daily_date==daily_date: self.album_daily_retry_at=0
                    elif kind=='action': self.action(value)
                    elif kind=='weather':
                        self.store,success=value;self.fetching=False
                        if self.page in PAGES: self.render(reason='weather')
                        self.startup_result('weather',success and self.store.view()[0] is not None)
                    elif kind=='bridge': self.bridge_event(value)
                    elif kind=='ended' and value[0] is self.process:
                        self.bridge_ended(value[1])
                    self.tick()
                    if self.connection=='已连接' and self.inflight is None and time.monotonic()-self.last_status>=60:
                        self.last_status=time.monotonic();self.send({'command':'status'})
                except Exception as exc:
                    if kind=='album_photo': self.album_error=f'跳过无法处理的照片：{exc}'
                    else: self.error=str(exc)
        self.send({'command':'close'})
        if self.process:
            try: self.process.wait(timeout=4)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try: self.process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()


class Server(ThreadingHTTPServer):
    daemon_threads=True
    allow_reuse_address=True


def control_url(port=18772, bind='0.0.0.0'):
    host=bind
    if bind=='0.0.0.0':
        try:
            with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as route:
                # UDP connect selects the default local interface without sending data.
                route.connect(('192.0.2.1',9));host=route.getsockname()[0]
        except OSError: host='127.0.0.1'
    return f'http://{host}:{port}'


def handler(app, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def reply(self,code,body,mime):
            self.send_response(code);self.send_header('Content-Type',mime);self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def trusted(self):
            local=self.connection.getsockname()[0]
            return self.headers.get('Host') in {f'{host}:{port}' for host in (local,'127.0.0.1','localhost')}
        def do_GET(self):
            if not self.trusted(): return self.reply(403,b'Forbidden','text/plain')
            path=urlsplit(self.path).path
            if path=='/': return self.reply(200,(HERE/'index.html').read_bytes(),'text/html; charset=utf-8')
            if path=='/control.js': return self.reply(200,(HERE/'control.js').read_bytes(),'text/javascript; charset=utf-8')
            if path=='/api/state': return self.reply(200,json.dumps(app.state(),ensure_ascii=False).encode(),'application/json; charset=utf-8')
            if path.startswith('/album/photo/'):
                try:
                    with app.lock:
                        photo_id=path.rsplit('/',1)[-1]
                        if not any(p['id']==photo_id for p in app.album.photos): raise ValueError()
                        image=app.album.path(photo_id).read_bytes()
                    return self.reply(200,image,'image/png')
                except (ValueError,OSError): return self.reply(404,b'Not found','text/plain')
            if path=='/preview.png':
                with app.lock: image=app.preview
                return self.reply(200,image,'image/png')
            return self.reply(404,b'Not found','text/plain')
        def album_request(self):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=MAX_UPLOAD: raise ValueError('每张照片最多20MB')
                data=self.rfile.read(length)
                if len(data)!=length: raise ValueError('上传未完成')
                with app.lock:
                    if self.path=='/api/album/upload':
                        app.album.add(data,unquote(self.headers.get('X-Photo-Name','photo')))
                    else:
                        if self.headers.get('Content-Type')!='application/json' or length>2048: raise ValueError('请求无效')
                        body=json.loads(data)
                        if self.path=='/api/album/select':
                            app.album.select(body['index']);app.page='album';app.remember_page()
                        elif self.path=='/api/album/delete': app.album.delete(body['id'])
                        elif self.path=='/api/album/source':
                            if app.album_syncing: raise ValueError('正在同步，请稍后更改来源')
                            app.album.set_source(body['source']);app.sync_album()
                        elif self.path=='/api/album/sync': app.sync_album()
                        elif self.path=='/api/album/settings': app.album.configure(body['fit'],body['interval'])
                        else: raise ValueError('请求无效')
                    app.album_changed()
                self.reply(200,b'{"ok":true}','application/json')
            except (ValueError,KeyError,TypeError,OSError) as exc:
                self.reply(400,json.dumps({'error':str(exc)},ensure_ascii=False).encode(),'application/json; charset=utf-8')
        def market_request(self):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if self.headers.get('Content-Type')!='application/json' or not 0<length<=2048: raise ValueError('请求无效')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict): raise ValueError('请求无效')
                now=datetime.now(TZ)
                if self.path=='/api/market/lookup':
                    codes=normalize_codes(body['code'])
                    if len(codes)!=1: raise ValueError('请一次输入一个代码')
                    try: row=fetch_quotes(codes)[0]
                    except Exception: raise ValueError('未识别到有效证券，请检查代码或稍后重试') from None
                    with app.lock:
                        app.market_candidates={k:v for k,v in app.market_candidates.items() if (now-v[1]).total_seconds()<120}
                        if len(app.market_candidates)>=32: app.market_candidates.clear()
                        app.market_candidates[row['code']]=(row,now)
                    return self.reply(200,json.dumps({'quote':row},ensure_ascii=False).encode(),'application/json; charset=utf-8')
                with app.lock:
                    if self.path=='/api/market/add':
                        candidate=app.market_candidates.get(body['code'])
                        if not candidate or (now-candidate[1]).total_seconds()>=120: raise ValueError('请先识别代码，识别结果有效期为两分钟')
                        app.market.add_quote(candidate[0],now)
                        app.market_candidates.pop(body['code'],None)
                    elif self.path=='/api/market/remove': app.market.remove(body['code'])
                    elif self.path=='/api/market/move': app.market.move(body['code'],body['direction'])
                    elif self.path=='/api/market/settings':
                        app.market.configure(body.get('codes',' '.join(app.market.codes)),body['interval'])
                    else: raise ValueError('请求无效')
                    if app.page=='market':
                        app.render(reason='market_settings')
                        if self.path in ('/api/market/add','/api/market/settings'): app.fetch_market()
                return self.reply(200,b'{"ok":true}','application/json')
            except (ValueError,KeyError,TypeError,OSError) as exc:
                return self.reply(400,json.dumps({'error':str(exc)},ensure_ascii=False).encode(),'application/json; charset=utf-8')

        def settings_request(self):
            try:
                length=int(self.headers.get('Content-Length','0'))
                if self.headers.get('Content-Type')!='application/json' or not 0<length<=2048:
                    raise ValueError('请求无效')
                body=json.loads(self.rfile.read(length))
                if not isinstance(body,dict): raise ValueError('请求无效')
                if self.path=='/api/weather/lookup':
                    query=validate_city(body.get('query'))
                    results=geocode_city(query)
                    return self.reply(200,json.dumps({'results':results},ensure_ascii=False).encode(),'application/json; charset=utf-8')
                with app.lock:
                    if self.path=='/api/weather/settings':
                        app.weather_settings(body)
                    elif self.path=='/api/device/settings':
                        app.device_settings(body)
                    else:
                        raise ValueError('请求无效')
                return self.reply(200,b'{"ok":true}','application/json')
            except (ValueError,TypeError,OSError) as exc:
                return self.reply(400,json.dumps({'error':str(exc)},ensure_ascii=False).encode(),'application/json; charset=utf-8')

        def do_POST(self):
            if not self.trusted(): return self.reply(403,b'Forbidden','text/plain')
            origin=self.headers.get('Origin')
            if (origin and origin!=f'http://{self.headers.get("Host")}') or self.headers.get('Sec-Fetch-Site')=='cross-site':
                return self.reply(403,b'Forbidden','text/plain')
            if self.path in ('/api/weather/settings','/api/weather/lookup','/api/device/settings'):
                return self.settings_request()
            if self.path=='/api/portfolio/settings':
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if self.headers.get('Content-Type')!='application/json' or not 0<length<=1024: raise ValueError('请求无效')
                    value=json.loads(self.rfile.read(length))
                    with app.lock:
                        app.portfolio.configure(value)
                        if app.page=='portfolio': app.render(reason='portfolio_settings');app.fetch_portfolio()
                    return self.reply(200,b'{"ok":true}','application/json')
                except (ValueError,TypeError,OSError) as exc:
                    return self.reply(400,json.dumps({'error':str(exc)},ensure_ascii=False).encode(),'application/json; charset=utf-8')
            if self.path.startswith('/api/album/'):
                return self.album_request()
            if self.path.startswith('/api/market/'):
                return self.market_request()
            if self.path!='/api/action' or self.headers.get('Content-Type')!='application/json':
                return self.reply(400,b'Invalid request','text/plain')
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<1024: raise ValueError()
                action=json.loads(self.rfile.read(length))['action']
                if action not in (*PAGES,'menu','market','market_refresh','sectors','sectors_refresh','news','news_refresh','gold','gold_refresh','portfolio','portfolio_refresh','album','album_play','album_pause','up','down','confirm','refresh','connect','disconnect','retry_display'): raise ValueError()
                app.events.put_nowait(('action',action))
            except (ValueError,KeyError,TypeError,queue.Full): return self.reply(400,b'Invalid action','text/plain')
            self.reply(202,b'{"accepted":true}','application/json')
    return Handler


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=18772);parser.add_argument('--connect',action='store_true')
    parser.add_argument('--bind',default='0.0.0.0',help='控制页监听IPv4地址，默认全部本机网卡')
    parser.add_argument('--page',choices=(*PAGES,'album','market','sectors','news','gold','portfolio','menu'),default=None,help='启动时显示的应用页面；省略时恢复上次页面')
    parser.add_argument('--config',type=Path);parser.add_argument('--data-dir',type=Path,default=DATA)
    args=parser.parse_args();config=DEFAULTS.copy()
    config_path=args.config or args.data_dir/'config.json'
    if config_path.exists(): config.update(json.loads(config_path.read_text()))
    if config['weather_interval_seconds']<60: parser.error('天气取数间隔至少60秒')
    app=App(config,args.data_dir,config_path)
    # Bind first: a second instance fails without taking another SDK session.
    server=Server((args.bind,args.port),handler(app,args.port))
    page=args.page or app.restore_page()
    if page!='home':
        app.page=page
        selected='home' if page in PAGES else page
        app.menu_index=next((i for i,item in enumerate(APPS) if item[0]==selected),0)
        app.render(reason='startup')
    if args.connect: app.request_startup_connect()
    thread=threading.Thread(target=app.run,daemon=True);thread.start()
    def shutdown(*_): raise KeyboardInterrupt
    signal.signal(signal.SIGTERM,shutdown)
    print(f'NOTE4 控制页：{control_url(args.port,args.bind)}/',flush=True)
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.server_close();app.stop.set();thread.join(timeout=7)


if __name__=='__main__': main()
