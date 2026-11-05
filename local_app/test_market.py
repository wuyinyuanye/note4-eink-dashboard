import io
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from app import App, DEFAULTS, Server, handler
from clock_page import TZ
from market import Market, normalize_codes, parse_quotes, session, auto_window, startup_quotes_current
from market_page import render_market

NOW = datetime(2026, 10, 22, 10, 30, tzinfo=TZ)


def quote(code='sh000001', price='3500', stamp=NOW, previous='3490', name='上证指数'):
    fields = [name, '3495', previous, price]+['0']*26+[stamp.strftime('%Y-%m-%d'), stamp.strftime('%H:%M:%S')]
    return 'var hq_str_'+code+'="'+','.join(fields)+'";'


class MarketTests(unittest.TestCase):
    def test_morning_restart_requires_complete_latest_trading_day(self):
        morning=NOW.replace(hour=6)
        yesterday=NOW.replace(day=21,hour=15)
        valid=parse_quotes(quote(stamp=yesterday),['sh000001'],morning)
        self.assertTrue(startup_quotes_current(valid,['sh000001'],morning))
        self.assertFalse(startup_quotes_current(valid,['sh000001','sz399001'],morning))
        stale=parse_quotes(quote(stamp=yesterday-timedelta(days=1)),['sh000001'],morning)
        self.assertFalse(startup_quotes_current(stale,['sh000001'],morning))
        self.assertFalse(startup_quotes_current(valid,['sh000001'],morning.replace(year=2027)))

    def test_codes_and_partial_invalid_quotes(self):
        self.assertEqual(normalize_codes('sh000001，510300;sz399001 sh000001'), ['sh000001','sh510300','sz399001'])
        for raw in ('', 'bj920001', 'sh000001&x=1', 'hello'):
            with self.assertRaises(ValueError): normalize_codes(raw)
        codes=['sh000001','sz399001']
        rows=parse_quotes(quote()+quote('sz399001', price='nan'), codes, NOW)
        self.assertEqual([r['code'] for r in rows], ['sh000001'])
        for body in (quote(price='inf'),quote(previous='0'),quote(price='-1'),quote(stamp=NOW+timedelta(minutes=3)), 'bad'):
            with self.assertRaises(ValueError): parse_quotes(body,codes,NOW)
        row=parse_quotes(quote(price='0'),codes,NOW)[0]
        self.assertEqual(row['price'],3490);self.assertFalse(row['traded'])

    def test_exchange_holidays_sessions_and_unknown_year(self):
        for value, expected in [('2026-10-01T10:00','休市'),('2026-10-10T10:00','休市'),
            ('2026-10-22T09:29','盘前'),('2026-10-22T09:30','交易中'),('2026-10-22T11:30','午间休市'),
            ('2026-10-22T13:00','交易中'),('2026-10-22T15:00','已收盘'),('2027-10-22T10:00','日历缺失')]:
            self.assertEqual(session(datetime.fromisoformat(value).replace(tzinfo=TZ))[0], expected)
        self.assertEqual(auto_window(NOW.replace(hour=15,minute=0,second=10)),(False,True))
        self.assertEqual(auto_window(NOW.replace(hour=12)),(False,False))

    def test_cache_schedule_failure_and_close_fetch_once(self):
        with tempfile.TemporaryDirectory() as d:
            m=Market(d);self.assertTrue(m.due(NOW))
            m.started(NOW);m.accept(parse_quotes(quote(),m.codes,NOW),NOW)
            self.assertFalse(m.due(NOW+timedelta(seconds=299)))
            self.assertTrue(m.due(NOW+timedelta(seconds=300)))
            restored=Market(d);self.assertEqual(restored.rows,m.rows)
            self.assertFalse(restored.due(NOW+timedelta(seconds=1)))
            restored.started(NOW+timedelta(minutes=5));restored.accept(None,NOW,error='offline')
            self.assertEqual(restored.rows,m.rows);self.assertFalse(restored.due(NOW+timedelta(minutes=6)))
            close=NOW.replace(hour=15,minute=0,second=10)
            self.assertTrue(restored.due(close));restored.started(close);restored.accept(None,close,error='offline')
            self.assertFalse(restored.due(close+timedelta(minutes=1)))
            self.assertFalse(restored.due(close+timedelta(hours=1)))
            self.assertEqual(restored.row_status(m.rows[0],NOW+timedelta(days=1)),'历史')
            self.assertEqual(restored.row_status(m.rows[0],NOW+timedelta(minutes=6)),'延迟')
            with patch.object(Path,'write_text',side_effect=OSError('disk')):
                restored.accept(parse_quotes(quote(price='3600'),m.codes,NOW),NOW)
            self.assertEqual(restored.rows,m.rows);self.assertIn('写入失败',restored.error)

    def test_render_empty_full_missing_history_and_large_numbers(self):
        with tempfile.TemporaryDirectory() as d:
            m=Market(d)
            self.assertEqual(render_market(m,NOW).size,(400,300))
            m.configure('sh000001 sz399001 sz399006 sh510300 sh518880 sz159915 sh600000',300)
            body=''.join(quote(c,price=str(3500+i*123),name='证券测试超长名字应该自动缩小') for i,c in enumerate(m.codes))
            m.accept(parse_quotes(body,m.codes,NOW),NOW)
            for page in (0,1):
                m.page=page
                for when in (NOW,NOW+timedelta(days=1)):
                    image=render_market(m,when);self.assertEqual(image.mode,'L')
            m.error='offline';render_market(m,NOW)
            m.rows[0]['price']=999999999
            m.page=0;render_market(m,NOW)

    def test_app_navigation_visibility_recording_and_result(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d))
            with patch.object(app,'fetch_market') as fetch:
                app.action('market');self.assertEqual(app.page,'market');fetch.assert_called_once()
                app.market.codes=['sh000001']*7
                app.action('down');self.assertEqual(app.market.page,1)
                app.action('up');self.assertEqual(app.market.page,0)
                app.action('confirm');self.assertEqual(fetch.call_count,2)
            rows=parse_quotes(quote(),['sh000001'],NOW)
            app.action('album')
            with patch.object(app,'render') as repaint:
                app.market_result((rows,NOW,None));repaint.assert_not_called()
                with patch.object(app,'fetch'):
                    app.tick(NOW,0)
                repaint.assert_not_called()
            app.page='market';app.recording=True;app.connection='已连接'
            with patch.object(app,'send') as send:
                app.market_result((rows,NOW,None));send.assert_not_called()
                app.bridge_event({'event':'recording_finished'});send.assert_called_once()
            app.bridge_event({'event':'key','key':'Up','action':'Long'})
            self.assertEqual(app.page,'menu')

    def test_weather_background_does_not_redraw_market(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d));app.page='market';app.events.put(('weather',(app.store,True)))
            with patch.object(app,'tick',side_effect=lambda:app.stop.set()),patch.object(app,'render') as repaint:
                app.run();repaint.assert_not_called()

    def test_settings_api_origin_validation_and_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d));server=Server(('127.0.0.1',0),handler(app,0))
            port=server.server_address[1];server.RequestHandlerClass=handler(app,port)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(body,origin=None):
                headers={'Content-Type':'application/json'}
                if origin: headers['Origin']=origin
                return urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/api/market/settings',data=json.dumps(body).encode(),headers=headers),timeout=3)
            try:
                with request({'codes':'sh510300 sz159915','interval':60}) as r: self.assertEqual(r.status,200)
                self.assertEqual(Market(d).codes,['sh510300','sz159915'])
                for body in ({'codes':'bad','interval':60},{'codes':'sh000001','interval':1}):
                    with self.assertRaises(urllib.error.HTTPError) as e: request(body)
                    self.assertEqual(e.exception.code,400)
                    e.exception.close()
                with self.assertRaises(urllib.error.HTTPError) as e: request({'codes':'sh000001','interval':60},'http://example.com')
                self.assertEqual(e.exception.code,403)
                e.exception.close()
                app.market.fetching=True
                with self.assertRaises(urllib.error.HTTPError) as e: request({'codes':'sh000001','interval':60})
                e.exception.close()
            finally:
                server.shutdown();server.server_close();thread.join(timeout=3)


if __name__=='__main__': unittest.main()
