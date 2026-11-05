"""Gray16 overview of SGE Au99.99, COMEX futures and 518880 ETF."""
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import re

from clock_page import TZ
from market_page import MarketCanvas
from weather_page import finish
from gold_feed import _price


def render_gold(quote, references=None):
    references = references or {}
    c = MarketCanvas()
    c.rounded((0, 0, 400, 31), 0, 34)
    c.text('黄金看板', 13, 5, 19, 255)
    c.text('Au99.99', 387, 10, 13, 221, anchor='right')
    values = None
    if quote is not None:
        required = ('latest', 'high', 'low', 'open', 'previous_close')
        try:
            values = {key: _price(str(quote[key])) for key in required}
            report_date = quote['report_date']
            if isinstance(report_date, str): report_date = date.fromisoformat(report_date)
            if not isinstance(report_date, date) or isinstance(report_date, datetime):
                raise ValueError('黄金行情日期无效')
            if values['high'] < values['low']: raise ValueError('黄金行情高低价异常')
        except (KeyError, TypeError):
            raise ValueError('黄金行情字段不完整') from None
    c.text('上金所最新价', 14, 41, 12, 102)
    c.text('元 / 克', 386, 41, 12, 102, anchor='right')
    c.text(format(values['latest'], '.2f') if values else '—', 200, 56, 39, 20, anchor='center')
    c.rounded((10, 105, 390, 194), 4, 241)
    c.line([(200, 110), (200, 189)], 204, 1)
    c.line([(20, 150), (380, 150)], 204, 1)
    for key, label, x, y in (('high','最高',105,110), ('low','最低',295,110),
                             ('open','今开',105,155), ('previous_close','昨收',295,155)):
        c.text(label, x, y, 11, 102, anchor='center')
        c.text(format(values[key], '.2f') if values else '—', x, y + 15, 22, 34, anchor='center')
    c.line([(200, 202), (200, 264)], 204, 1)
    for key, title, unit, x in (('comex', 'COMEX 期金', '美元/盎司', 105),
                                 ('etf', '518880 ETF', '元/份', 295)):
        entry = references.get(key)
        if key == 'comex' and entry:
            match = re.fullmatch(r'Gold (Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) (\d{2})', entry.get('contract', ''))
            if match:
                month = ('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec').index(match.group(1)) + 1
                title = f'COMEX {match.group(2)}{month:02}'
        c.text(title + ' · ' + unit, x, 202, 11, 85, anchor='center')
        if entry:
            try:
                amount = _price(str(entry['price']))
                stamp = datetime.fromisoformat(entry['stamp'])
                if stamp.tzinfo is None: raise ValueError('报价时间缺少时区')
                when = stamp.astimezone(TZ).strftime('%m/%d %H:%M')
                value = format(amount, '.2f' if key == 'comex' else '.3f')
                source = {'CNBC': 'CNBC', 'Yahoo Finance': '雅虎', '东方财富': '东财'}.get(entry.get('source'), '行情源') if key == 'comex' else '腾讯'
                note = source + ' ' + when
            except (KeyError, ValueError, TypeError, InvalidOperation):
                value, note = '—', '报价不可用'
        else:
            value, note = '—', '暂无报价'
        c.text(value, x, 219, 23, 34, anchor='center')
        c.text(note, x, 250, 10, 102, anchor='center')
    c.rounded((0, 276, 400, 300), 0, 51)
    if values:
        c.text('上金所 ' + report_date.strftime('%Y-%m-%d'), 12, 282, 11, 238)
        source = '官方报盘' if quote.get('official') else '新浪 · 上金所行情'
    else:
        c.text('上金所暂无报价', 12, 282, 11, 238)
        source = '确认键更新'
    c.text(source, 387, 282, 11, 238, anchor='right')
    return finish(c)
