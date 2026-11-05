"""Explicitly requested Au99.99 updates, with an atomic private cache."""
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from gold_feed import _price, CONTRACT
from clock_page import TZ
from gold_references import normalize_reference


def normalize_quote(quote):
    if not isinstance(quote, dict) or quote.get('contract') != CONTRACT:
        raise ValueError('黄金合约无效')
    try:
        day = quote['report_date']
        if isinstance(day, datetime): raise ValueError('行情日期无效')
        day = day if isinstance(day, date) else date.fromisoformat(day)
        fetched = quote['fetched_at']
        fetched = fetched if isinstance(fetched, datetime) else datetime.fromisoformat(fetched)
        if fetched.tzinfo is None or fetched.utcoffset() is None:
            raise ValueError('获取时间缺少时区')
        numbers = {key: _price(str(quote[key])) for key in ('latest', 'high', 'low', 'open', 'previous_close')}
        if numbers['high'] < numbers['low']: raise ValueError('高低价异常')
        if quote.get('official') is True:
            source = '上海黄金交易所'
        elif quote.get('official') is False:
            source = '新浪财经 · 上金所行情'
        else:
            raise ValueError('行情来源无效')
        if quote.get('source') != source: raise ValueError('行情来源不匹配')
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError('黄金行情无效') from exc
    return {'contract': CONTRACT, 'report_date': day.isoformat(),
            'fetched_at': fetched.astimezone(TZ).isoformat(), 'source': source,
            'official': quote['official'], **{key: str(value) for key, value in numbers.items()}}


class Gold:
    def __init__(self, directory):
        self.path = Path(directory) / 'gold.json'
        self.quote = None
        self.references = {}
        self.error = None
        self.fetching = False
        self.last_attempt = None
        try:
            saved = json.loads(self.path.read_text())
            quote = normalize_quote(saved['quote']) if saved.get('quote') else None
            refs = {key: normalize_reference(key, value) for key, value in saved.get('references', {}).items()}
            now = datetime.now(TZ)
            timestamps = [datetime.fromisoformat(v['stamp']) for v in refs.values()]
            if quote: timestamps.append(datetime.fromisoformat(quote['fetched_at']))
            if any(stamp > now + timedelta(minutes=2) for stamp in timestamps):
                raise ValueError('获取时间异常')
            self.quote, self.references = quote, refs
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.error = '黄金缓存无效，请重新获取'

    def started(self, now):
        self.fetching = True
        self.last_attempt = now.isoformat()

    def accept(self, results):
        """Commit each successful source; retain the last valid value on failure."""
        self.fetching = False
        quote, refs = self.quote, dict(self.references)
        failures = []
        changed = False
        for key, label in (('quote', '上金所'), ('comex', 'COMEX'), ('etf', '518880')):
            try:
                value = results[key]
                if value is None: raise ValueError('无报价')
                if key == 'quote': quote = normalize_quote(value)
                else: refs[key] = normalize_reference(key, value)
                changed = True
            except (KeyError, ValueError, TypeError):
                failures.append(label)
        if changed:
            temp = self.path.with_suffix('.tmp')
            try:
                temp.write_text(json.dumps({'quote': quote, 'references': refs}, ensure_ascii=False, indent=2))
                temp.replace(self.path)
            except OSError:
                self.error = '黄金缓存写入失败' + ('，已保留上次内容' if self.quote or self.references else '，请稍后重试')
                return False
            self.quote, self.references = quote, refs
        self.error = ('、'.join(failures) + '获取失败' +
                      ('，保留已有报价' if quote or refs else '，请稍后重试') if failures else None)
        return changed

    def state(self):
        return {'quote': self.quote, 'references': self.references, 'fetching': self.fetching,
                'error': self.error, 'requested_at': self.last_attempt, 'automatic': False}
