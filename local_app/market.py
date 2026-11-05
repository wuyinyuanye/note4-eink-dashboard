"""Read-only mainland quotes, private cache and exchange-calendar scheduling.

No cloud push code or credentials. All times are Asia/Shanghai.
"""
from __future__ import annotations
import json
import math
import re
import subprocess
from datetime import date, datetime, time, timedelta
from pathlib import Path
from functools import lru_cache
from clock_page import HERE, TZ

DEFAULT_CODES = ['sh000001', 'sz399001', 'sz399006']
INTERVALS = (60, 300, 900)


def normalize_codes(raw):
    if not isinstance(raw, str):
        raise ValueError('请输入沪深代码，以空格或逗号分隔')
    codes = []
    for code in filter(None, re.split(r'[\s,，;；]+', raw.strip().lower())):
        if re.fullmatch(r'\d{6}', code):
            code = ('sh' if code[0] in '569' else 'sz') + code
        if not re.fullmatch(r'(sh|sz)\d{6}', code):
            raise ValueError('支持沪深股票、ETF及指数，如 sh000001、sz399001、sh510300')
        if code not in codes:
            codes.append(code)
    if not 1 <= len(codes) <= 18:
        raise ValueError('请选择1–18个代码，每页最多6项；指数请带sh/sz前缀')
    return codes


@lru_cache(maxsize=4)
def holidays_for(year):
    try:
        calendar = json.loads((HERE/'market_calendar'/f'{year}.json').read_text())
        holidays = calendar['holidays']
        if calendar['year'] != year or not 15 <= len(holidays) <= 60:
            raise ValueError()
        if not all(date.fromisoformat(d).year == year for d in holidays):
            raise ValueError()
        return frozenset(holidays)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def session(now):
    now = now.astimezone(TZ)
    holidays = holidays_for(now.year)
    if holidays is None:
        return '日历缺失', False
    if now.weekday() >= 5 or now.date().isoformat() in holidays:
        return '休市', False
    clock = now.time().replace(tzinfo=None)
    if clock < time(9, 30): return '盘前', False
    if clock < time(11, 30): return '交易中', True
    if clock < time(13): return '午间休市', False
    if clock < time(15): return '交易中', True
    return '已收盘', False


def auto_window(now):
    label, live = session(now)
    # One final fetch at each session boundary, allowing the source to settle.
    t = now.astimezone(TZ).time().replace(tzinfo=None)
    closing = label in ('午间休市', '已收盘') and (
        time(11, 30, 10) <= t < time(11, 40) or time(15, 0, 10) <= t < time(15, 10))
    return live, closing


def startup_quotes_current(rows, codes, now):
    """Require every quote from the latest confirmed Shanghai trading day."""
    now=now.astimezone(TZ)
    day=now.date()
    if now.time().replace(tzinfo=None)<time(9,30): day-=timedelta(days=1)
    for _ in range(20):
        holidays=holidays_for(day.year)
        if holidays is None: return False
        if day.weekday()<5 and day.isoformat() not in holidays: break
        day-=timedelta(days=1)
    else:
        return False
    try:
        lookup={row['code']:validate_row(row,now) for row in rows}
        return all(code in lookup and datetime.fromisoformat(lookup[code]['stamp']).astimezone(TZ).date()==day
                   for code in codes)
    except (ValueError,TypeError,KeyError,AttributeError):
        return False


def validate_row(row, now):
    if not isinstance(row, dict) or not re.fullmatch(r'(sh|sz)\d{6}', row.get('code', '')):
        raise ValueError('无效代码')
    stamp = datetime.fromisoformat(row['stamp'])
    if stamp.tzinfo is None or stamp > now + timedelta(minutes=2):
        raise ValueError('报价时间异常')
    for key in ('price', 'previous'):
        value = row[key]
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('无效价格')
    if not isinstance(row['name'], str) or not 1 <= len(row['name']) <= 80:
        raise ValueError('无效名称')
    if type(row['traded']) is not bool:
        raise ValueError('无效成交状态')
    if not math.isfinite((row['price']/row['previous']-1)*100):
        raise ValueError('涨跌幅无效')
    return {k: row[k] for k in ('code', 'name', 'stamp', 'price', 'previous', 'traded')}


def parse_quotes(body, codes, now):
    records = dict(re.findall(r'var hq_str_(\w+)="([^"\r\n]*)";', body))
    rows = []
    for code in codes:
        fields = records.get(code, '').split(',')
        try:
            previous, current = float(fields[2]), float(fields[3])
            if not math.isfinite(current) or current < 0: raise ValueError()
            stamp = datetime.strptime(fields[30]+' '+fields[31], '%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ)
            rows.append(validate_row(dict(code=code, name=fields[0], previous=previous,
                price=current if current else previous, traded=current > 0, stamp=stamp.isoformat()), now))
        except (IndexError, ValueError, TypeError, OverflowError):
            continue
    if not rows:
        raise ValueError('未取得有效报价，请检查代码或稍后重试')
    return rows


def fetch_quotes(codes, now=None):
    result = subprocess.run(['curl', '--fail', '--silent', '--show-error',
        '--connect-timeout', '5', '--max-time', '15', '--max-filesize', '1000000',
        '-H', 'Referer: https://finance.sina.com.cn', '-H', 'User-Agent: Mozilla/5.0',
        'https://hq.sinajs.cn/list='+','.join(codes)], capture_output=True, timeout=18, check=True)
    return parse_quotes(result.stdout.decode('gb18030'), codes, now or datetime.now(TZ))


class Market:
    def __init__(self, directory):
        self.path = Path(directory)/'market.json'
        self.codes = DEFAULT_CODES.copy()
        self.interval = 300
        self.rows = []
        self.fetched_at = None
        self.error = None
        self.page = 0
        self.fetching = False
        self.last_attempt = None
        self.last_refresh_trigger = None
        self.close_attempt = None
        try:
            saved = json.loads(self.path.read_text())
            self.codes = normalize_codes(saved['codes'])
            if type(saved['interval']) is not int or saved['interval'] not in INTERVALS: raise ValueError()
            self.interval = saved['interval']
            now = datetime.now(TZ)
            rows = [validate_row(r, now) for r in saved['rows']]
            fetched = datetime.fromisoformat(saved['fetched_at']) if saved.get('fetched_at') else None
            if fetched and (fetched.tzinfo is None or fetched > now + timedelta(minutes=2)): raise ValueError()
            self.rows = [r for r in rows if r['code'] in self.codes]
            self.fetched_at = fetched.isoformat() if fetched else None
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.rows = []; self.fetched_at = None
            self.error = '行情缓存无效，请重新获取'

    def save(self):
        content = dict(codes=' '.join(self.codes), interval=self.interval, rows=self.rows, fetched_at=self.fetched_at)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(content, ensure_ascii=False, indent=2))
        temporary.replace(self.path)

    def configure(self, raw, interval, quotes=()):
        if self.fetching: raise ValueError('正在获取行情，请稍后修改')
        codes = normalize_codes(raw)
        if type(interval) is not int or interval not in INTERVALS: raise ValueError('更新间隔请选择1、5或15分钟')
        changed = codes != self.codes
        old = self.codes, self.interval, self.rows, self.fetched_at, self.page
        self.codes, self.interval = codes, interval
        if changed:
            lookup = {r['code']: r for r in (*self.rows, *quotes)}
            self.rows = [lookup[c] for c in codes if c in lookup]
            if set(codes)-set(old[0]): self.fetched_at = None
            self.page = min(self.page, (len(codes)-1)//6)
        try: self.save()
        except OSError:
            self.codes, self.interval, self.rows, self.fetched_at, self.page = old
            raise
        if changed: self.last_attempt = None; self.close_attempt = None; self.error = None

    def add_quote(self, row, now):
        row = validate_row(row, now)
        if row['code'] in self.codes: raise ValueError('这只证券已在自选中')
        self.configure(' '.join([*self.codes, row['code']]), self.interval, quotes=(row,))

    def remove(self, code):
        if code not in self.codes: raise ValueError('证券已不在自选中，请刷新列表')
        if len(self.codes)==1: raise ValueError('至少保留一只证券')
        self.configure(' '.join(c for c in self.codes if c!=code), self.interval)

    def move(self, code, direction):
        if code not in self.codes or type(direction) is not int or direction not in (-1,1):
            raise ValueError('排序请求无效')
        codes=self.codes.copy();i=codes.index(code);j=i+direction
        if not 0<=j<len(codes): raise ValueError('已到列表边界')
        codes[i],codes[j]=codes[j],codes[i]
        self.configure(' '.join(codes),self.interval)

    def due(self, now):
        if self.fetching: return False
        live, closing = auto_window(now)
        elapsed = (now-self.last_attempt).total_seconds() if self.last_attempt else float('inf')
        if live:
            if self.fetched_at and (now-datetime.fromisoformat(self.fetched_at)).total_seconds() < self.interval:
                return False
            return elapsed >= self.interval
        key = now.strftime('%Y-%m-%d') + session(now)[0]
        return closing and self.close_attempt != key and elapsed >= 10

    def started(self, now):
        self.fetching = True; self.last_attempt = now
        if auto_window(now)[1]: self.close_attempt = now.strftime('%Y-%m-%d')+session(now)[0]

    def accept(self, rows, now, error=None):
        self.fetching = False
        if error:
            self.error = error
            return
        previous = self.rows, self.fetched_at
        self.rows, self.fetched_at = rows, now.isoformat()
        try: self.save()
        except OSError:
            self.rows, self.fetched_at = previous
            self.error = '行情缓存写入失败，保留旧数据'
            return
        self.error = None

    def row_status(self, row, now):
        stamp = datetime.fromisoformat(row['stamp'])
        if stamp.date() != now.date(): return '历史'
        if session(now)[1] and (now-stamp).total_seconds() > 300: return '延迟'
        return '' if row['traded'] else '昨收'

    def state(self, now):
        return dict(codes=self.codes, interval=self.interval, rows=self.rows, fetched_at=self.fetched_at,
            error=self.error, fetching=self.fetching, page=self.page, pages=(len(self.codes)+5)//6,
            session=session(now)[0],source='新浪行情',last_refresh_trigger=self.last_refresh_trigger,
            requested_at=self.last_attempt.isoformat() if self.last_attempt else None,
            calendar_source='https://www.sse.com.cn/disclosure/dealinstruc/closed/')
