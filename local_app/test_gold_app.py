import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from app import App, DEFAULTS
from clock_page import TZ
from gold import Gold
from gold_feed import parse_sina_quote
from gold_references import parse_comex, parse_etf
from test_gold_feed import SINA
from test_gold_references import NOW, yahoo, tencent


def results():
    quote = parse_sina_quote(SINA)
    quote['fetched_at'] = NOW
    return {'quote': quote, 'comex': parse_comex(yahoo(), NOW), 'etf': parse_etf(tencent(), NOW)}


class GoldAppTests(unittest.TestCase):
    def test_partial_failure_retains_each_previous_value_and_persists(self):
        with tempfile.TemporaryDirectory() as directory:
            gold = Gold(directory)
            self.assertTrue(gold.accept(results()))
            previous = gold.quote.copy(), dict(gold.references)
            newer = results()
            newer['quote'] = None
            newer['comex'] = None
            newer['etf']['price'] = '8.500'
            self.assertTrue(gold.accept(newer))
            self.assertEqual(gold.quote, previous[0])
            self.assertEqual(gold.references['comex'], previous[1]['comex'])
            self.assertEqual(gold.references['etf']['price'], '8.500')
            self.assertIn('COMEX', gold.error)
            reloaded = Gold(directory)
            self.assertEqual(reloaded.quote, gold.quote)
            self.assertEqual(reloaded.references, gold.references)

    def test_no_success_and_disk_failure_preserve_values(self):
        with tempfile.TemporaryDirectory() as directory:
            gold = Gold(directory); gold.accept(results())
            before = gold.quote.copy(), dict(gold.references), gold.path.read_bytes()
            self.assertFalse(gold.accept({}))
            self.assertEqual((gold.quote, gold.references, gold.path.read_bytes()), before)
            with patch.object(Path, 'write_text', side_effect=OSError('disk full')):
                self.assertFalse(gold.accept(results()))
            self.assertEqual((gold.quote, gold.references, gold.path.read_bytes()), before)

    def test_gold_is_gray16_and_does_not_automatically_refresh_on_tick(self):
        with tempfile.TemporaryDirectory() as directory:
            app = App(DEFAULTS, Path(directory))
            with patch.object(app, 'fetch_gold') as refresh, patch.object(app, 'fetch'):
                app.action('gold')
                self.assertEqual((app.page, app.preview_format), ('gold', 'Gray16'))
                refresh.assert_called_once()
                refresh.reset_mock()
                app.tick(now=NOW, monotonic_now=10_000)
                refresh.assert_not_called()
                app.action('confirm')
                refresh.assert_called_once()
            self.assertFalse(app.gold.state()['automatic'])

    def test_result_for_other_page_cannot_change_display(self):
        with tempfile.TemporaryDirectory() as directory:
            app = App(DEFAULTS, Path(directory))
            before = app.preview, app.revision, app.page
            app.gold_result(results())
            self.assertEqual((app.preview, app.revision, app.page), before)

    def test_all_source_failures_keep_display_and_show_error(self):
        with tempfile.TemporaryDirectory() as directory:
            app = App(DEFAULTS, Path(directory)); app.gold.accept(results())
            app.page = 'gold'; app.render(now=NOW)
            before = app.preview, app.revision
            with patch.object(app, 'dispatch') as dispatch:
                app.gold_result({})
            self.assertEqual((app.preview, app.revision), before)
            dispatch.assert_not_called()
            self.assertIn('获取失败', app.gold.error)


if __name__ == '__main__':
    unittest.main()
