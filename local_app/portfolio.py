"""Verified mainland ETF/stock position valuation and a private cache."""
import json
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from clock_page import TZ
from market import normalize_codes, holidays_for


def positive(value, label, maximum='1000000000'):
    if isinstance(value, bool) or len(str(value))>32: raise ValueError(label+'无效')
    try:
        number=Decimal(str(value))
        if not number.is_finite() or not 0<number<=Decimal(maximum) or number.as_tuple().exponent < -6:
            raise ValueError(label+'必须为有效正数，最多六位小数')
        return number
    except InvalidOperation:
        raise ValueError(label+'必须为有效数字') from None


def validate_config(value):
    if not isinstance(value,dict): raise ValueError('持仓配置无效')
    codes=normalize_codes(value.get('code',''))
    if len(codes)!=1 or codes[0].startswith(('sh000','sz399')):
        raise ValueError('请选择一只沪深股票或 ETF，不支持指数')
    return {'code':codes[0], 'quantity':str(positive(value.get('quantity'),'持有份额')),
            'cost_price':str(positive(value.get('cost_price'),'成本价','1000000'))}


def calculate(config, price):
    quantity=positive(config['quantity'],'持有份额');cost=positive(config['cost_price'],'成本价')
    price=positive(price,'行情价格')
    return {'market_value':quantity*price,'cost_total':quantity*cost,
            'pnl':quantity*(price-cost),'return_pct':(price/cost-1)*100}


def quote_reference(now):
    now=now.astimezone(TZ);day=now.date()
    for _ in range(20):
        holidays=holidays_for(day.year)
        if holidays is None: raise ValueError('交易日历缺失，暂停估值')
        if day.weekday()<5 and day.isoformat() not in holidays and (day<now.date() or (now.hour,now.minute)>=(9,30)):
            if day<now.date() or now.hour>=15: return datetime(day.year,day.month,day.day,15,tzinfo=TZ)
            if (now.hour,now.minute)>=(11,30) and now.hour<13: return now.replace(hour=11,minute=30,second=0,microsecond=0)
            return now
        day-=timedelta(days=1)
    raise ValueError('无法确认最近交易日')


def parse_quote(body, code, source):
    if source=='腾讯':
        match=re.search(r'v_'+re.escape(code)+r'="([^"\r\n]*)"',body)
        fields=match.group(1).split('~') if match else []
        if len(fields)<31 or fields[2]!=code[2:]: raise ValueError('报价代码不匹配')
        name,price,stamp=fields[1],fields[3],fields[30]
        stamp=datetime.strptime(stamp,'%Y%m%d%H%M%S').replace(tzinfo=TZ)
    elif source=='新浪':
        match=re.search(r'var hq_str_'+re.escape(code)+r'="([^"\r\n]*)"',body)
        fields=match.group(1).split(',') if match else []
        if len(fields)<32: raise ValueError('报价代码不匹配')
        name,price=fields[0],fields[3]
        stamp=datetime.strptime(fields[30]+' '+fields[31],'%Y-%m-%d %H:%M:%S').replace(tzinfo=TZ)
    else: raise ValueError('来源无效')
    if not isinstance(name,str) or not 1<=len(name)<=80: raise ValueError('名称无效')
    return {'code':code,'name':name,'price':str(positive(price,'行情价格')),
            'stamp':stamp.isoformat(),'source':source}


def valid_quote(value, code, now, reference):
    if not isinstance(value,dict) or value.get('code')!=code or value.get('source') not in ('腾讯','新浪'):
        raise ValueError('报价身份无效')
    name=value.get('name');stamp=datetime.fromisoformat(value['stamp'])
    if not isinstance(name,str) or not 1<=len(name)<=80 or stamp.tzinfo is None: raise ValueError('报价无效')
    if stamp.astimezone(TZ).date()!=reference.date() or (stamp-now).total_seconds()>120 or (reference-stamp).total_seconds()>300:
        raise ValueError('行情不是最新有效交易日报价')
    return {'code':code,'name':name,'price':str(positive(value['price'],'行情价格')),
            'stamp':stamp.astimezone(TZ).isoformat(),'source':value['source']}


def fetch_quote(code, now=None):
    now=now or datetime.now(TZ);reference=quote_reference(now)
    def fetch(source):
        url=('https://qt.gtimg.cn/q=' if source=='腾讯' else 'https://hq.sinajs.cn/list=')+code
        request=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://finance.sina.com.cn/'})
        with urllib.request.urlopen(request,timeout=12) as response: payload=response.read(65537)
        if len(payload)>65536: raise ValueError('响应过大')
        return valid_quote(parse_quote(payload.decode('gb18030'),code,source),code,now,reference)
    quotes=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs=[pool.submit(fetch,source) for source in ('腾讯','新浪')]
        for job in jobs:
            try: quotes.append(job.result())
            except (OSError,UnicodeError,ValueError,KeyError,TypeError,OverflowError): pass
    if not quotes: raise ValueError('行情源没有最新有效报价，保留原图')
    return max(quotes,key=lambda quote:quote['stamp'])


class Portfolio:
    interval=300
    def __init__(self,directory):
        self.path=Path(directory)/'portfolio.json';self.config=None;self.quote=None
        self.error=None;self.fetching=False;self.last_attempt=None
        try:
            saved=json.loads(self.path.read_text());self.config=validate_config(saved['config'])
            q=saved.get('quote')
            if q:
                stamp=datetime.fromisoformat(q['stamp'])
                if stamp.tzinfo is None or stamp>datetime.now(TZ)+timedelta(minutes=2): raise ValueError('报价时间无效')
                if q.get('code')!=self.config['code'] or q.get('source') not in ('腾讯','新浪') or not isinstance(q.get('name'),str) or not 1<=len(q['name'])<=80: raise ValueError('报价无效')
                positive(q['price'],'行情价格');self.quote=q
        except FileNotFoundError: pass
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            self.config=None;self.quote=None;self.error='持仓缓存无效，请重新设置'

    def save(self,config,quote):
        temporary=self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps({'config':config,'quote':quote},ensure_ascii=False,indent=2))
        temporary.replace(self.path)

    def configure(self, value):
        if self.fetching: raise ValueError('正在更新，请稍后保存')
        config=validate_config(value);self.save(config,None)
        self.config=config;self.quote=None;self.error=None;self.last_attempt=None

    def due(self, now):
        if not self.config or self.fetching: return False
        return self.last_attempt is None or (now-self.last_attempt).total_seconds()>=self.interval

    def started(self,now): self.fetching=True;self.last_attempt=now

    def accept(self,quote,now,error=None):
        self.fetching=False
        if error:
            self.error=error+('，已保留上次内容' if self.quote else '');return False
        try:
            quote=valid_quote(quote,self.config['code'],now,quote_reference(now));self.save(self.config,quote)
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            self.error='持仓报价无效或缓存写入失败'+('，已保留上次内容' if self.quote else '');return False
        self.quote=quote;self.error=None;return True

    def state(self):
        return {'config':self.config,'quote':self.quote,'error':self.error,'fetching':self.fetching,
                'interval':self.interval,'requested_at':self.last_attempt.isoformat() if self.last_attempt else None}
