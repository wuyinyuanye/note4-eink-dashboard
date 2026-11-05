"""Explicitly identified COMEX gold futures and Shanghai gold ETF quotes."""
import json
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from clock_page import TZ

YAHOO_URL = 'https://query1.finance.yahoo.com/v8/finance/chart/GC%3DF?range=1d&interval=1m'
CNBC_URL = 'https://quote.cnbc.com/quote-html-webservice/quote.htm?symbols=@GC.1'
EASTMONEY_URL = ('https://push2.eastmoney.com/api/qt/stock/get?secid=101.GC00Y'
                 '&ut=fa5fd1943c7b386f172d6893dbfba10b'
                 '&fields=f43,f57,f58,f59,f86')
TENCENT_URL = 'https://qt.gtimg.cn/q=sh518880'


def _price(value):
    if isinstance(value, bool): raise ValueError('报价无效')
    try:
        price = Decimal(str(value))
        if not price.is_finite() or not Decimal('0') < price < Decimal('1000000'):
            raise ValueError('报价无效')
        return price
    except (InvalidOperation, TypeError):
        raise ValueError('报价无效') from None


def _timestamp(stamp, now):
    if stamp.tzinfo is None or not -120 <= (now - stamp).total_seconds() <= 14 * 86400:
        raise ValueError('报价时间无效或已过期')
    return stamp.astimezone(TZ)


def normalize_reference(kind, value):
    """Validate a cached quote without pretending that an old price is current."""
    if not isinstance(value, dict) or value.get('kind') != kind:
        raise ValueError('报价身份无效')
    try:
        stamp = datetime.fromisoformat(value['stamp'])
        if stamp.tzinfo is None or stamp.utcoffset() is None: raise ValueError('报价时间无效')
        price = _price(value['price'])
        if kind == 'comex':
            contract = value['contract']
            source = value.get('source')
            valid_contract = (
                isinstance(contract, str) and
                (re.fullmatch(r'Gold (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{2}', contract)
                 or (source == '东方财富' and contract == 'GC00Y')))
            if (not valid_contract or source not in ('Yahoo Finance', 'CNBC', '东方财富')
                    or value.get('unit') != '美元/金衡盎司'):
                raise ValueError('期货合约无效')
        elif kind == 'etf':
            if (value.get('contract') != '518880' or value.get('source') != '腾讯财经'
                    or value.get('unit') != '元/份'):
                raise ValueError('基金身份无效')
        else:
            raise ValueError('报价类型无效')
        return {'kind': kind, 'price': str(price), 'stamp': stamp.astimezone(TZ).isoformat(),
                'source': value['source'], 'contract': value['contract'], 'unit': value['unit']}
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError('报价数据无效') from exc


def parse_comex(payload, now):
    try:
        result = json.loads(payload)['chart']['result']
        if not isinstance(result, list) or len(result) != 1: raise ValueError('结果无效')
        meta = result[0]['meta']
        if (meta['symbol'] != 'GC=F' or meta['instrumentType'] != 'FUTURE'
                or meta['fullExchangeName'] != 'COMEX' or meta['currency'] != 'USD'):
            raise ValueError('合约身份不匹配')
        name = meta.get('shortName', '')
        if not isinstance(name, str) or not re.fullmatch(r'Gold (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{2}', name):
            raise ValueError('合约名称无效')
        raw_time = meta['regularMarketTime']
        if isinstance(raw_time, bool) or not isinstance(raw_time, int): raise ValueError('时间无效')
        stamp = _timestamp(datetime.fromtimestamp(raw_time, TZ), now)
        price = _price(meta['regularMarketPrice'])
        return {'kind': 'comex', 'price': str(price), 'stamp': stamp.isoformat(),
                'source': 'Yahoo Finance', 'contract': name, 'unit': '美元/金衡盎司'}
    except (KeyError, TypeError, IndexError, ValueError, OverflowError, OSError) as exc:
        raise ValueError('COMEX 行情不可用') from exc


def parse_comex_cnbc(payload, now):
    """Parse CNBC's explicitly labelled front-month COMEX gold future."""
    months = {
        'F': ('Jan', 1), 'G': ('Feb', 2), 'H': ('Mar', 3), 'J': ('Apr', 4),
        'K': ('May', 5), 'M': ('Jun', 6), 'N': ('Jul', 7), 'Q': ('Aug', 8),
        'U': ('Sep', 9), 'V': ('Oct', 10), 'X': ('Nov', 11), 'Z': ('Dec', 12),
    }
    try:
        quote = ET.fromstring(payload).find('.//quickQuote')
        if quote is None: raise ValueError('结果无效')

        def field(name):
            node = quote.find(name)
            if node is None or node.text is None: raise ValueError('字段缺失')
            return node.text.strip()

        alt_symbol = field('altSymbol')
        symbol = re.fullmatch(r'GC([FGHJKMNQUVXZ])(\d{2})', alt_symbol)
        if symbol is None or field('assetSubType') != 'Future' or field('assetType') != 'DERIVATIVE':
            raise ValueError('合约身份不匹配')
        if field('exchange') != 'CEC:Commodities Exchange Centre' or field('currencyCode') != 'USD':
            raise ValueError('交易所或币种不匹配')
        month_name, _ = months[symbol.group(1)]
        expected_name = f"Gold COMEX ({month_name}'{symbol.group(2)})"
        if field('name') != expected_name:
            raise ValueError('合约名称不匹配')
        raw_time = int(field('last_time_msec'))
        stamp = _timestamp(datetime.fromtimestamp(raw_time / 1000, TZ), now)
        return {'kind': 'comex', 'price': str(_price(field('last'))), 'stamp': stamp.isoformat(),
                'source': 'CNBC', 'contract': f'Gold {month_name} {symbol.group(2)}',
                'unit': '美元/金衡盎司'}
    except (ET.ParseError, KeyError, TypeError, IndexError, ValueError, OverflowError, OSError) as exc:
        raise ValueError('COMEX 行情不可用') from exc


def parse_comex_eastmoney(payload, now):
    """Fallback for the COMEX gold continuous contract GC00Y."""
    try:
        data = json.loads(payload)
        item = data['data']
        if data.get('rc') != 0 or item['f57'] != 'GC00Y' or item['f58'] != 'COMEX黄金':
            raise ValueError('合约身份不匹配')
        decimals = item['f59']
        raw_price = item['f43']
        raw_time = item['f86']
        if (type(decimals) is not int or not 0 <= decimals <= 6
                or type(raw_price) is not int or type(raw_time) is not int):
            raise ValueError('报价字段无效')
        price = Decimal(raw_price).scaleb(-decimals)
        stamp = _timestamp(datetime.fromtimestamp(raw_time, TZ), now)
        return {'kind': 'comex', 'price': str(_price(price)), 'stamp': stamp.isoformat(),
                'source': '东方财富', 'contract': 'GC00Y', 'unit': '美元/金衡盎司'}
    except (KeyError, TypeError, IndexError, ValueError, OverflowError, OSError) as exc:
        raise ValueError('COMEX 行情不可用') from exc


def parse_etf(payload, now):
    if isinstance(payload, bytes): payload = payload.decode('gb18030', errors='replace')
    match = re.search(r'v_sh518880="([^"\r\n]*)"', payload)
    fields = match.group(1).split('~') if match else []
    try:
        if len(fields) <= 30 or fields[2] != '518880' or '黄金ETF' not in fields[1]:
            raise ValueError('基金身份不匹配')
        stamp = _timestamp(datetime.strptime(fields[30], '%Y%m%d%H%M%S').replace(tzinfo=TZ), now)
        return {'kind': 'etf', 'price': str(_price(fields[3])), 'stamp': stamp.isoformat(),
                'source': '腾讯财经', 'contract': '518880', 'unit': '元/份'}
    except (ValueError, IndexError) as exc:
        raise ValueError('518880 行情不可用') from exc


def fetch_reference(kind, now=None):
    now = now or datetime.now(TZ)
    if kind not in ('comex', 'etf'): raise ValueError('报价类型无效')
    def fetch(url):
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', 'Accept': '*/*'})
        with urllib.request.urlopen(req, timeout=10) as response:
            payload = response.read(1_000_001)
        if len(payload) > 1_000_000: raise ValueError('报价响应过大')
        return payload

    if kind == 'etf':
        return parse_etf(fetch(TENCENT_URL), now)
    for url, parser in ((CNBC_URL, parse_comex_cnbc),
                        (EASTMONEY_URL, parse_comex_eastmoney),
                        (YAHOO_URL, parse_comex)):
        try:
            return parser(fetch(url), now)
        except Exception:
            continue
    raise ValueError('COMEX 行情不可用')
