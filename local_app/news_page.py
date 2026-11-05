"""Domestic and international news, composed for the 400×300 gray display."""
from datetime import datetime

from clock_page import SCALE, TZ
from market_page import MarketCanvas, market_font
from news import INTERVAL
from weather_page import finish


def compact_title(text, width, size):
    """Keep both the subject and the ending when one line must be shortened."""
    face = market_font(size, SCALE)
    if face.getlength(text) <= width * SCALE:
        return text
    tail = text[-7:]
    prefix = text[:-7]
    while prefix and face.getlength(prefix + '…' + tail) > width * SCALE:
        prefix = prefix[:-1]
    return prefix + '…' + tail


def wrap_title(text, width, size):
    face = market_font(size, SCALE)
    lines, current = [], ''
    for ch in text:
        if current and face.getlength(current + ch) > width * SCALE:
            lines.append(current)
            current = ch
        else:
            current += ch
    if current:
        lines.append(current)
    if len(lines) > 2:
        last = lines[1]
        while last and face.getlength(last + '…') > width * SCALE:
            last = last[:-1]
        return [lines[0], last + '…']
    return lines


def render_news(news, now):
    now = now.astimezone(TZ)
    c = MarketCanvas()
    c.rounded((0, 0, 400, 30), 0, 34)
    c.text('即时新闻', 11, 5, 19, 255)
    stamp = (datetime.fromisoformat(news.fetched_at).astimezone(TZ).strftime('%m/%d %H:%M')
             if news.fetched_at else '尚未更新')
    if news.error:
        stamp = '更新失败 · ' + stamp
    elif news.fetched_at and (now - datetime.fromisoformat(news.fetched_at)).total_seconds() >= INTERVAL * 2:
        stamp = '缓存 · ' + stamp
    c.text(stamp, 389, 10, 11, 238, anchor='right')
    if not news.rows:
        c.text('暂无新闻', 200, 121, 20, 68, anchor='center')
        c.text('确认键更新 · 来源：中新网', 200, 155, 12, 119, anchor='center')
        if news.error:
            c.text('获取失败，请稍后重试', 200, 180, 12, 85, anchor='center')
        return finish(c)

    for index, (source, label) in enumerate((('domestic', '国内'), ('world', '国际'))):
        top = 36 + index * 131
        c.rounded((10, top - 1, 390, top + 15), 2, 231)
        c.text(label, 15, top, 14, 34)
        c.text('中新网', 385, top + 3, 10, 102, anchor='right')
        group = [row for row in news.rows if row['source'] == source]
        wrapped = [wrap_title(row['title'], 333, 16) for row in group]
        for i, lines in enumerate(wrapped):
            if len(lines) == 2 and len(lines[1]) <= 2:
                wrapped[i] = [compact_title(group[i]['title'], 333, 16)]
        while sum(map(len, wrapped)) > 6:
            candidates = [i for i, lines in enumerate(wrapped) if len(lines) > 1]
            chosen = min(candidates, key=lambda i: len(group[i]['title']))
            wrapped[chosen] = [compact_title(group[chosen]['title'], 333, 16)]
        y = top + 18
        for row, lines in zip(group, wrapped):
            published = datetime.fromisoformat(row['published_at']).astimezone(TZ)
            when = published.strftime('%H:%M' if published.date() == now.date() else '%m-%d')
            c.text(when, 390, y + 2, 10, 102, anchor='right')
            for line_index, line in enumerate(lines):
                c.text(line, 10, y + line_index * 17, 16, 25)
            y += len(lines) * 17 + 2
    return finish(c)
