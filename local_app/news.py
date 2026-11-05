"""Recent domestic and international reporting with an atomic local cache."""
import concurrent.futures
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit
from clock_page import TZ

INTERVAL = 600
COUNT = 5

SOURCES = (('domestic', '国内'), ('world', '国际'))
SOURCE_COUNTS = {'domestic': 5, 'world': 5}
FEEDS = ('china', 'world', 'scroll-news')
BASE_URL = 'https://www.chinanews.com.cn/rss/'
MAX_AGE = 24 * 3600


def fetch_feed(feed):
    request = urllib.request.Request(BASE_URL + feed + '.xml', headers={
        'User-Agent': 'Mozilla/5.0', 'Accept': 'application/rss+xml, application/xml',
        'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=10) as response:
        payload = response.read(2_000_001)
    if len(payload) > 2_000_000:
        raise ValueError('新闻响应过大')
    root = ET.fromstring(payload)
    if root.tag != 'rss':
        raise ValueError('新闻源格式异常')
    rows, now = [], time.time()
    for item in root.findall('./channel/item'):
        try:
            title = ' '.join(item.findtext('title', '').split())
            url = item.findtext('link', '').strip()
            parts = urlsplit(url)
            if parts.scheme not in ('http', 'https') or parts.hostname not in ('www.chinanews.com.cn', 'www.chinanews.com'):
                continue
            section = parts.path.split('/')[1]
            category = ('world' if feed == 'world' or section == 'gj' else
                        'domestic' if feed == 'china' or section in ('gn', 'sh', 'cj', 'dwq') else None)
            published = parsedate_to_datetime(item.findtext('pubDate', ''))
            if published.tzinfo is None:
                continue
            stamp = published.timestamp()
            if not category or not title or len(title) > 2000 or not -120 <= now - stamp <= MAX_AGE:
                continue
            rows.append(dict(source=category, title=title, url=url,
                             published_at=published.astimezone(TZ).isoformat(),
                             updated_time=stamp * 1000, provider='中新网'))
        except (ValueError, TypeError, IndexError, OverflowError):
            continue
    return rows


def same_story(left, right):
    if left['url'] == right['url']:
        return True
    normalize = lambda s: re.sub(r'[^\w]', '', s).lower()
    a, b = normalize(left['title']), normalize(right['title'])
    return a == b or SequenceMatcher(None, a, b).ratio() >= .72


def fetch_news():
    # Category feeds plus the rolling feed provide newer domestic stories and
    # tolerate a single endpoint failure. Never relabel old reports as new.
    candidates = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        jobs = [pool.submit(fetch_feed, feed) for feed in FEEDS]
        for job in jobs:
            try:
                candidates.extend(job.result())
            except (OSError, ValueError, ET.ParseError):
                pass
    selected = []
    for source, name in SOURCES:
        group = []
        for row in sorted((r for r in candidates if r['source'] == source),
                          key=lambda r: r['updated_time'], reverse=True):
            if any(same_story(row, old) for old in selected + group):
                continue
            group.append(dict(row, source_name=name, rank=len(group) + 1))
            if len(group) == SOURCE_COUNTS[source]:
                break
        if len(group) < SOURCE_COUNTS[source]:
            raise ValueError(f'{name}近24小时有效新闻不足，保留原图，请稍后重试。')
        selected.extend(group)
    return validate_rows(selected)


def validate_rows(rows):
    expected = [(source, rank) for source, _ in SOURCES for rank in range(1, COUNT + 1)]
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('新闻数据无效')
    if [(row.get('source'), row.get('rank')) for row in rows] != expected:
        raise ValueError('新闻必须包含国内、国际各五条')
    result = []
    names = dict(SOURCES)
    for row in rows:
        title, rank, url = row.get('title'), row.get('rank'), row.get('url')
        if not isinstance(title, str) or type(rank) is not int:
            raise ValueError('新闻标题或排序无效')
        title = ' '.join(title.split())
        parts = urlsplit(url) if isinstance(url, str) else None
        if (not title or len(title) > 2000 or row.get('source_name') != names[row['source']]
                or not parts or parts.scheme not in ('http', 'https')
                or parts.hostname not in ('www.chinanews.com.cn', 'www.chinanews.com')):
            raise ValueError('新闻标题或来源无效')
        try:
            published = datetime.fromisoformat(row['published_at'])
        except (KeyError, ValueError, TypeError):
            raise ValueError('新闻发布时间无效') from None
        if published.tzinfo is None or published.utcoffset() is None:
            raise ValueError('新闻发布时间缺少时区')
        result.append(dict(source=row['source'], source_name=row['source_name'], rank=rank,
                           title=title, url=url, published_at=published.isoformat()))
    return result

class News:
    def __init__(self, directory):
        self.path = Path(directory) / 'news.json'
        self.rows = []
        self.fetched_at = None
        self.error = None
        self.fetching = False
        self.last_attempt = None
        self.last_refresh_trigger = None
        try:
            saved = json.loads(self.path.read_text())
            rows = validate_rows(saved['rows'])
            fetched = datetime.fromisoformat(saved['fetched_at'])
            now = datetime.now(TZ)
            if fetched.tzinfo is None or fetched > now + timedelta(minutes=2):
                raise ValueError()
            self.rows, self.fetched_at = rows, fetched.isoformat()
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            self.error = '新闻缓存无效，请重新获取'

    def due(self, now):
        if self.fetching:
            return False
        references = [value for value in (
            datetime.fromisoformat(self.fetched_at) if self.fetched_at else None,
            self.last_attempt,
        ) if value is not None]
        reference = max(references) if references else None
        return reference is None or (now - reference).total_seconds() >= INTERVAL

    def started(self, now, trigger):
        self.fetching = True
        self.last_attempt = now
        self.last_refresh_trigger = trigger

    def accept(self, rows, now, error=None):
        self.fetching = False
        if error:
            self.error = error + ('，已保留上次内容' if self.rows else '')
            return False
        try:
            rows = validate_rows(rows)
        except ValueError:
            self.error = '新闻数据无效' + ('，已保留上次内容' if self.rows else '，请重新获取')
            return False
        previous = self.rows, self.fetched_at
        self.rows, self.fetched_at = rows, now.isoformat()
        temporary = self.path.with_suffix('.tmp')
        try:
            temporary.write_text(json.dumps({'rows': rows, 'fetched_at': self.fetched_at}, ensure_ascii=False, indent=2))
            temporary.replace(self.path)
        except OSError:
            self.rows, self.fetched_at = previous
            self.error = '新闻缓存写入失败' + ('，已保留上次内容' if self.rows else '，请重试')
            return False
        self.error = None
        return True

    def state(self):
        return dict(rows=self.rows, fetched_at=self.fetched_at, error=self.error, fetching=self.fetching,
                    interval=INTERVAL, source='中新网', last_refresh_trigger=self.last_refresh_trigger,
                    requested_at=self.last_attempt.isoformat() if self.last_attempt else None)
