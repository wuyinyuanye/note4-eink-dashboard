"""Shanghai Gold Exchange Au99.99 dashboard with an official previous close."""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser


from clock_page import TZ


BASE_URL = "https://www.sge.com.cn"
DELAYED_PATH = "/sjzx/yshqbg"
HISTORY_PATH = "/sjzx/quotation_daily_new"
SINA_URL = "https://hq.sinajs.cn/list=gds_AU9999"
CONTRACT = "Au99.99"
MAX_REPORT_AGE_DAYS = 14
SGE_TIMEOUT_SECONDS = 6
SINA_TIMEOUT_SECONDS = 10


class _ReportParser(HTMLParser):
    """Collect visible table cells and the first page title."""

    def __init__(self):
        super().__init__()
        self.rows: list[list[str]] = []
        self.heading = ""
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._heading: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
            self.rows.append(self._row)
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag == "h1" and not self.heading:
            self._heading = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)
        if self._heading is not None:
            self._heading.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr":
            self._row = None
        elif tag == "h1" and self._heading is not None:
            self.heading = " ".join("".join(self._heading).split())
            self._heading = None


def parse_report(payload):
    if not isinstance(payload, bytes):
        payload = payload.encode()
    if len(payload) > 2_000_000:
        raise ValueError("上金所页面过大")
    parser = _ReportParser()
    parser.feed(payload.decode("utf-8"))
    return parser.heading, parser.rows


def fetch_page(path, params=None):
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Cache-Control": "no-cache",
        },
    )
    error = None
    for _ in range(2):
        try:
            with urllib.request.urlopen(
                request, timeout=SGE_TIMEOUT_SECONDS
            ) as response:
                payload = response.read(2_000_001)
            return payload
        except (OSError, UnicodeError) as exc:
            error = exc
    raise ValueError("上海黄金交易所行情暂不可用，保留原图。") from error


def fetch_sina_page():
    """Fetch the same Au99.99 exchange fields from an accessible mirror."""
    request = urllib.request.Request(
        SINA_URL,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "text/plain,*/*",
            "Referer": "https://finance.sina.com.cn/",
            "Cache-Control": "no-cache",
        },
    )
    error = None
    for _ in range(2):
        try:
            with urllib.request.urlopen(
                request, timeout=SINA_TIMEOUT_SECONDS
            ) as response:
                payload = response.read(128_001)
            if len(payload) > 128_000:
                raise ValueError("备用行情响应过大")
            return payload
        except (OSError, UnicodeError) as exc:
            error = exc
    raise ValueError("新浪财经黄金行情暂不可用") from error


def _price(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("上金所价格为空")
    try:
        number = Decimal(value.replace(",", "").strip())
    except InvalidOperation:
        raise ValueError("上金所价格格式无效") from None
    if not number.is_finite() or not Decimal("0") < number < Decimal("1000000"):
        raise ValueError("上金所价格无效")
    return number


def parse_delayed(payload):
    heading, rows = parse_report(payload)
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日", heading)
    if not match:
        raise ValueError("无法确认上金所行情日期")
    report_date = datetime(*(int(part) for part in match.groups()), tzinfo=TZ).date()
    row = next((row for row in rows if len(row) >= 5 and row[0] == CONTRACT), None)
    if row is None:
        raise ValueError("上金所页面缺少 Au99.99 行情")
    latest, high, low, opening = map(_price, row[1:5])
    if high < low:
        raise ValueError("上金所高低价异常")
    return {
        "contract": CONTRACT,
        "report_date": report_date,
        "latest": latest,
        "high": high,
        "low": low,
        "open": opening,
    }


def parse_previous_close(payload, before):
    _, rows = parse_report(payload)
    available = []
    for row in rows:
        if len(row) < 6 or row[1] != CONTRACT:
            continue
        try:
            day = datetime.strptime(row[0], "%Y-%m-%d").date()
            close = _price(row[5])
        except (ValueError, TypeError):
            continue
        if day < before:
            available.append((day, close))
    if not available:
        raise ValueError("上金所未返回上一交易日收盘价")
    day, close = max(available, key=lambda item: item[0])
    return day, close


def parse_sina_quote(payload):
    """Parse Sina's Au99.99 feed, which mirrors the SGE delayed report."""
    if not isinstance(payload, bytes):
        payload = payload.encode()
    if len(payload) > 128_000:
        raise ValueError("备用行情响应过大")
    text = payload.decode("gb18030", errors="replace")
    match = re.search(r'var\s+hq_str_gds_AU9999="([^"]*)"', text)
    if not match:
        raise ValueError("备用行情缺少 Au99.99 报价")
    fields = match.group(1).split(",")
    if fields == [""] or len(fields) < 13:
        raise ValueError("备用行情字段不完整")
    try:
        report_date = datetime.strptime(fields[12], "%Y-%m-%d").date()
    except ValueError:
        raise ValueError("无法确认备用行情日期") from None
    latest, high, low, opening, previous_close = map(
        _price, (fields[0], fields[4], fields[5], fields[8], fields[7])
    )
    if high < low:
        raise ValueError("备用行情高低价异常")
    return {
        "contract": CONTRACT,
        "report_date": report_date,
        "latest": latest,
        "high": high,
        "low": low,
        "open": opening,
        "previous_close": previous_close,
        "previous_date": None,
        "source": "新浪财经 · 上金所行情",
        "official": False,
    }


def _fetch_sge_quote(now):
    start = (now.date() - timedelta(days=21)).isoformat()
    end = now.date().isoformat()
    with ThreadPoolExecutor(max_workers=2) as pool:
        delayed_job = pool.submit(fetch_page, DELAYED_PATH)
        history_job = pool.submit(
            fetch_page,
            HISTORY_PATH,
            {"start_date": start, "end_date": end},
        )
        delayed = delayed_job.result()
        history = history_job.result()
    quote = parse_delayed(delayed)
    previous_date, previous_close = parse_previous_close(history, quote["report_date"])
    quote.update(previous_close=previous_close, previous_date=previous_date,
                 source="上海黄金交易所", official=True)
    return quote


def _validate_report_date(quote, now):
    report_date = quote.get("report_date")
    if not isinstance(report_date, date):
        raise ValueError("黄金行情日期无效")
    age = (now.date() - report_date).days
    if age < 0 or age > MAX_REPORT_AGE_DAYS:
        raise ValueError("黄金行情日期已过期，保留原图。")


def fetch_quote(now=None):
    """Prefer the exchange site and fall back to its Au99.99 quote mirror."""
    now = now or datetime.now(TZ)
    try:
        quote = _fetch_sge_quote(now)
        _validate_report_date(quote, now)
    except (OSError, ValueError):
        try:
            quote = parse_sina_quote(fetch_sina_page())
            _validate_report_date(quote, now)
        except (OSError, ValueError) as exc:
            raise ValueError(
                "上金所及新浪备用行情暂不可用，保留原图。"
            ) from exc
    quote["fetched_at"] = now
    return quote

