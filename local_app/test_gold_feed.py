import tempfile
import unittest
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import gold_feed
from clock_page import TZ
from gold_page import render_gold

SINA = ('var hq_str_gds_AU9999="864.70,0,864.45,864.70,873.80,863.05,'
        '15:30:06,868.99,869.30,800426,115.00,4.00,2026-10-29,黄金99";').encode()
DELAYED = ('<html><h1>上海黄金交易所2026年10月29日延时行情</h1>'
           '<table><tr><td>Au99.99</td><td>864.7</td><td>873.8</td>'
           '<td>863.05</td><td>869.3</td></tr></table></html>').encode()
HISTORY = ('<html><table><tr><td>2026-10-28</td><td>Au99.99</td>'
           '<td>862.50</td><td>873.00</td><td>860.20</td><td>868.99</td>'
           '</tr></table></html>').encode()


class GoldTests(unittest.TestCase):
    def test_official_and_fallback_fields_match(self):
        official = gold_feed.parse_delayed(DELAYED)
        prior_date, close = gold_feed.parse_previous_close(HISTORY, official['report_date'])
        fallback = gold_feed.parse_sina_quote(SINA)
        for key in ('latest', 'high', 'low', 'open'):
            self.assertEqual(official[key], fallback[key])
        self.assertEqual((prior_date, close), (date(2026, 10, 28), fallback['previous_close']))
        self.assertFalse(fallback['official'])

    def test_stale_and_missing_sources_fail_instead_of_returning_placeholder(self):
        now = datetime(2026, 11, 20, tzinfo=TZ)
        with patch.object(gold_feed, '_fetch_sge_quote', side_effect=ValueError('offline')), \
             patch.object(gold_feed, 'fetch_sina_page', return_value=SINA):
            with self.assertRaisesRegex(ValueError, '保留原图'):
                gold_feed.fetch_quote(now)

    def test_bad_values_or_missing_dates_are_rejected(self):
        with self.assertRaises(ValueError): gold_feed.parse_sina_quote(SINA.replace(b'873.80', b'800.00'))
        with self.assertRaises(ValueError): render_gold({'latest': Decimal('1')})

    def test_gray_page_and_empty_state_are_400_by_300(self):
        for quote in (None, gold_feed.parse_sina_quote(SINA)):
            image = render_gold(quote)
            self.assertEqual((image.size, image.mode), ((400, 300), 'L'))
            quantized = {round(i / 17) * 17 for i, count in enumerate(image.histogram()) if count}
            self.assertGreaterEqual(len(quantized), 8)


if __name__ == '__main__':
    unittest.main()
