import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app import App, DEFAULTS
from clock_page import TZ
from news import COUNT, SOURCES, INTERVAL, News, fetch_news, validate_rows
from news_page import render_news, compact_title
from market_page import market_font


NOW = datetime(2026, 10, 23, 12, 0, tzinfo=TZ)


def rows(long=False):
    suffix = '：这是用于验证长标题省略与中英文 GLP-1 1234567890 排版' * (5 if long else 1)
    return [dict(source=source, source_name=name, rank=rank, title=f'{name}第{rank}条{suffix}',
                 url=f'https://www.chinanews.com.cn/{source}/{rank}.shtml', published_at=NOW.isoformat())
            for source, name in SOURCES for rank in range(1, COUNT + 1)]


class NewsTests(unittest.TestCase):
    def test_failed_stale_cache_waits_full_interval_before_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            news = News(directory)
            news.accept(rows(), NOW - timedelta(hours=1))
            self.assertTrue(news.due(NOW))
            news.started(NOW, 'auto')
            self.assertFalse(news.due(NOW + timedelta(seconds=INTERVAL)))
            news.accept(None, NOW + timedelta(seconds=5), '获取失败')
            for seconds in (6, 60, INTERVAL - 1):
                self.assertFalse(news.due(NOW + timedelta(seconds=seconds)))
            self.assertTrue(news.due(NOW + timedelta(seconds=INTERVAL)))

    def test_invalid_payload_and_disk_failure_preserve_last_success(self):
        with tempfile.TemporaryDirectory() as directory:
            news = News(directory)
            news.accept(rows(), NOW)
            before = news.rows, news.fetched_at, news.path.read_bytes()
            for invalid in ([None] * 9, [dict(item, title=None) for item in rows()],
                            [dict(item, rank=True) for item in rows()]):
                with self.subTest(invalid=invalid):
                    with self.assertRaises(ValueError): validate_rows(invalid)
                    news.started(NOW + timedelta(minutes=10), 'manual')
                    news.accept(invalid, NOW + timedelta(minutes=10))
                    self.assertFalse(news.fetching)
                    self.assertIn('数据无效', news.error)
                    self.assertEqual((news.rows, news.fetched_at, news.path.read_bytes()), before)
            with patch.object(Path, 'write_text', side_effect=OSError('disk full')):
                news.accept(rows(long=True), NOW + timedelta(minutes=10))
            self.assertIn('缓存写入失败', news.error)
            self.assertEqual((news.rows, news.fetched_at, news.path.read_bytes()), before)

    def test_corrupt_cache_is_recoverable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'news.json'
            for payload in ({'rows': [None] * 9, 'fetched_at': NOW.isoformat()},
                            {'rows': rows(), 'fetched_at': '2026-10-23T12:00:00'}):
                path.write_text(json.dumps(payload))
                news = News(directory)
                self.assertFalse(news.rows)
                self.assertIn('缓存无效', news.error)
                self.assertTrue(news.due(NOW))

    def test_long_titles_fit_without_changing_short_titles(self):
        for title in ('短标题', '中英文 GLP-1 / DeepSeek ' * 80):
            result = compact_title(title, 262, 17)
            self.assertLessEqual(market_font(17, 4).getlength(result), 262 * 4)
            if title == '短标题':
                self.assertEqual(result, title)
            else:
                self.assertIn('…', result)

    def test_background_news_result_does_not_replace_other_application(self):
        with tempfile.TemporaryDirectory() as directory:
            app = App(DEFAULTS, Path(directory))
            app.action('menu')
            before = app.preview, app.revision, app.page
            app.news_result((rows(), NOW, None))
            self.assertEqual((app.preview, app.revision, app.page), before)

    def test_failed_update_keeps_the_current_image_and_does_not_dispatch(self):
        with tempfile.TemporaryDirectory() as directory:
            app = App(DEFAULTS, Path(directory))
            app.news.accept(rows(), NOW)
            app.page = 'news'
            app.render(now=NOW)
            before = app.preview, app.revision
            with patch.object(app, 'dispatch') as dispatch:
                app.news_result((None, NOW + timedelta(minutes=10), '获取失败'))
            self.assertEqual((app.preview, app.revision), before)
            dispatch.assert_not_called()
            self.assertIn('保留上次内容', app.news.error)

    def test_fetch_sorts_deduplicates_and_tolerates_one_failed_feed(self):
        import news
        titles = ['政策发布', '经济观察', '暴雨预警', '科研突破', '教育改革',
                  '选举揭晓', '航线恢复', '南极科考', '海外降息', '多国合作']
        candidates = [dict(row, title=titles[i], updated_time=i) for i, row in enumerate(rows())]
        with patch.object(news, 'fetch_feed', side_effect=[OSError(), candidates, candidates]):
            result = fetch_news()
        self.assertEqual([r['title'] for r in result],
                         [titles[i] for i in [4, 3, 2, 1, 0, 9, 8, 7, 6, 5]])
        self.assertEqual(len(result), 10)

    def test_incomplete_category_is_rejected(self):
        import news
        candidates = [dict(row, updated_time=i) for i, row in enumerate(rows()[:4])]
        with patch.object(news, 'fetch_feed', return_value=candidates):
            with self.assertRaisesRegex(ValueError, '保留原图'):
                fetch_news()

    def test_rss_filters_invalid_and_stale_reports(self):
        import news
        now = datetime.now(TZ)
        items = []
        for title, stamp in [('新报道', now), ('旧报道', now-timedelta(days=2)),
                             ('未来报道', now+timedelta(hours=1))]:
            items.append(f'<item><title>{title}</title><link>https://www.chinanews.com.cn/gj/1.shtml</link><pubDate>{stamp.strftime("%a, %d %b %Y %H:%M:%S %z")}</pubDate></item>')
        payload = ('<rss><channel>' + ''.join(items) + '</channel></rss>').encode()
        with patch.object(news.urllib.request, 'urlopen', return_value=io.BytesIO(payload)):
            result = news.fetch_feed('scroll-news')
        self.assertEqual([r['title'] for r in result], ['新报道'])
        self.assertEqual(result[0]['source'], 'world')

    def test_missing_source_rejects_whole_update_and_keeps_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            news = News(directory)
            news.accept(rows(), NOW)
            previous = news.rows.copy()
            news.started(NOW + timedelta(minutes=10), 'auto')
            news.accept(None, NOW + timedelta(minutes=10), '国际新闻获取失败')
            self.assertEqual(news.rows, previous)
            self.assertIn('保留上次内容', news.error)
            self.assertFalse(news.fetching)
            self.assertEqual(News(directory).rows, previous)

    def test_newspaper_layout_handles_empty_and_long_titles(self):
        with tempfile.TemporaryDirectory() as directory:
            news = News(directory)
            for content in ([], rows(long=True)):
                news.rows = content
                news.fetched_at = NOW.isoformat() if content else None
                image = render_news(news, NOW)
                self.assertEqual((image.size, image.mode), ((400, 300), 'L'))
                quantized={round(value/17)*17 for value in image.getdata()}
                self.assertGreaterEqual(len(quantized), 4)
                self.assertLessEqual(len(quantized), 16)

    def test_news_page_is_gray16_and_confirm_refreshes(self):
        with tempfile.TemporaryDirectory() as directory:
            app = App(DEFAULTS, Path(directory))
            with patch.object(app, 'fetch_news') as refresh:
                app.action('news')
                self.assertEqual(app.page, 'news')
                self.assertEqual(app.preview_format, 'Gray16')
                refresh.assert_called_once()
                refresh.reset_mock()
                app.action('up');app.action('down')
                self.assertEqual(app.page, 'news')
                refresh.assert_not_called()
                app.action('confirm')
                refresh.assert_called_once_with(trigger='confirm')

if __name__ == '__main__':
    unittest.main()
