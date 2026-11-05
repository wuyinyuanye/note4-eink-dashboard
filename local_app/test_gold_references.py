import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from clock_page import TZ
from gold_references import parse_comex, parse_comex_cnbc, parse_comex_eastmoney, parse_etf, fetch_reference

NOW = datetime(2026, 11, 5, 9, 30, tzinfo=TZ)


def yahoo(**changes):
    meta = {'symbol': 'GC=F', 'instrumentType': 'FUTURE', 'fullExchangeName': 'COMEX',
            'currency': 'USD', 'shortName': 'Gold Dec 26',
            'regularMarketTime': int((NOW-timedelta(minutes=10)).timestamp()),
            'regularMarketPrice': 4118.7}
    meta.update(changes)
    return json.dumps({'chart': {'result': [{'meta': meta}]}}).encode()


def tencent(**changes):
    fields = [''] * 31
    fields[1] = '黄金ETF华安'; fields[2] = '518880'; fields[3] = '8.400'
    fields[30] = '20261105092744'
    for i, value in changes.items(): fields[int(i)] = value
    return ('v_sh518880="' + '~'.join(fields) + '";').encode('gb18030')


def cnbc(**changes):
    values = {
        'altSymbol': 'GCZ26', 'assetSubType': 'Future', 'assetType': 'DERIVATIVE',
        'exchange': 'CEC:Commodities Exchange Centre', 'currencyCode': 'USD',
        'name': "Gold COMEX (Dec'26)", 'last': '4138.5',
        'last_time_msec': str(int((NOW-timedelta(minutes=10)).timestamp()*1000)),
    }
    values.update(changes)
    fields = ''.join(f'<{key}>{value}</{key}>' for key, value in values.items())
    return f'<QuickQuoteResult><quickQuote>{fields}</quickQuote></QuickQuoteResult>'.encode()


def eastmoney(**changes):
    item = {'f43': 41385, 'f57': 'GC00Y', 'f58': 'COMEX黄金', 'f59': 1,
            'f86': int((NOW-timedelta(minutes=10)).timestamp())}
    item.update(changes)
    return json.dumps({'rc': 0, 'data': item},ensure_ascii=False).encode()


class ReferenceTests(unittest.TestCase):
    def test_exact_futures_identity_and_reported_timestamp(self):
        quote = parse_comex(yahoo(), NOW)
        self.assertEqual((quote['contract'], quote['unit'], quote['price']),
                         ('Gold Dec 26', '美元/金衡盎司', '4118.7'))
        self.assertEqual(datetime.fromisoformat(quote['stamp']), NOW-timedelta(minutes=10))
        for changes in ({'instrumentType': 'CFD'}, {'fullExchangeName': 'NYQ'},
                        {'shortName': 'Gold'}, {'currency': 'CNY'}, {'regularMarketPrice': 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_comex(yahoo(**changes), NOW)

    def test_wrong_future_or_outdated_timestamp_is_rejected(self):
        for when in (NOW+timedelta(minutes=3), NOW-timedelta(days=15)):
            with self.assertRaises(ValueError):
                parse_comex(yahoo(regularMarketTime=int(when.timestamp())), NOW)

    def test_cnbc_parser_requires_explicit_comex_future_identity(self):
        quote = parse_comex_cnbc(cnbc(),NOW)
        self.assertEqual((quote['source'],quote['contract'],quote['price']),
                         ('CNBC','Gold Dec 26','4138.5'))
        for changes in ({'assetSubType':'CFD'}, {'exchange':'NYMEX'},
                        {'currencyCode':'CNY'}, {'altSymbol':'CLZ26'},
                        {'name':"Gold COMEX (Nov'26)"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                parse_comex_cnbc(cnbc(**changes),NOW)

    def test_eastmoney_fallback_matches_continuous_comex_gold_contract(self):
        quote = parse_comex_eastmoney(eastmoney(),NOW)
        self.assertEqual((quote['source'],quote['contract'],quote['price']),
                         ('东方财富','GC00Y','4138.5'))
        with self.assertRaises(ValueError):
            parse_comex_eastmoney(eastmoney(**{'f58':'纽约黄金'}),NOW)

    def test_etf_is_not_gold_price_per_gram(self):
        quote = parse_etf(tencent(), NOW)
        self.assertEqual((quote['contract'], quote['unit'], quote['price']), ('518880', '元/份', '8.400'))
        with self.assertRaises(ValueError): parse_etf(tencent(**{'2':'518881'}), NOW)
        with self.assertRaises(ValueError): parse_etf(tencent(**{'30':'20251001090000'}), NOW)

    def test_unknown_kind_never_requests_network(self):
        with patch('gold_references.urllib.request.urlopen') as request:
            with self.assertRaises(ValueError): fetch_reference('cfd', NOW)
            request.assert_not_called()


if __name__ == '__main__':
    unittest.main()
