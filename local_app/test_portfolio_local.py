import io
import tempfile
import json
import threading
import urllib.request
import urllib.error
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from app import App, DEFAULTS, Server, handler
from clock_page import TZ
from portfolio import Portfolio, calculate, fetch_quote, quote_reference, validate_config
from portfolio_page import money, render_portfolio

NOW=datetime(2026,10,23,16,0,tzinfo=TZ)
CONFIG={'code':'518880','quantity':'8385','cost_price':'8.865'}
QUOTE={'code':'sh518880','name':'黄金ETF','price':'8.199','stamp':'2026-10-23T15:15:00+08:00','source':'腾讯'}


class PortfolioTests(unittest.TestCase):
    def test_http_settings_are_validated_and_invalid_change_is_not_saved(self):
        with tempfile.TemporaryDirectory() as directory:
            app=App(DEFAULTS,Path(directory));server=Server(('127.0.0.1',0),handler(app,0))
            port=server.server_port;server.RequestHandlerClass=handler(app,port)
            worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
            def save(value):
                request=urllib.request.Request(f'http://127.0.0.1:{port}/api/portfolio/settings',
                    data=json.dumps(value).encode(),headers={'Content-Type':'application/json'},method='POST')
                return urllib.request.urlopen(request,timeout=2)
            try:
                with save(CONFIG) as response:self.assertEqual(response.status,200)
                before=app.portfolio.path.read_bytes()
                with self.assertRaises(urllib.error.HTTPError) as error:save({**CONFIG,'quantity':'NaN'})
                self.assertEqual(error.exception.code,400);error.exception.close()
                self.assertEqual(app.portfolio.path.read_bytes(),before)
            finally:
                server.shutdown();server.server_close();worker.join(timeout=3)

    def test_calculation_and_input_validation(self):
        config=validate_config(CONFIG);values=calculate(config,'8.199')
        self.assertEqual(money(values['pnl']),'-5,584.41')
        self.assertEqual(money(values['market_value']),'68,748.62')
        for invalid in ('NaN','Infinity','0','-1',True,None,'0.0000001'):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                validate_config({**CONFIG,'cost_price':invalid})
        with self.assertRaises(ValueError):validate_config({**CONFIG,'code':'sh000001'})

    def test_session_reference_rejects_unknown_calendar(self):
        self.assertEqual(quote_reference(NOW).strftime('%Y-%m-%d %H:%M'),'2026-10-23 15:00')
        self.assertEqual(quote_reference(NOW.replace(day=24,hour=8)).strftime('%Y-%m-%d %H:%M'),'2026-10-23 15:00')
        with patch('portfolio.holidays_for',return_value=None), self.assertRaises(ValueError):quote_reference(NOW)

    def test_only_working_source_retains_correct_label(self):
        fields=['']*31;fields[1]='黄金ETF';fields[2]='518880';fields[3]='8.199';fields[30]='20261023151500'
        payload=('v_sh518880="'+'~'.join(fields)+'";').encode('gb18030')
        def response(request, timeout):
            if 'gtimg' in request.full_url: return io.BytesIO(payload)
            raise OSError('offline')
        with patch('portfolio.urllib.request.urlopen',side_effect=response):
            result=fetch_quote('sh518880',NOW)
        self.assertEqual((result['source'],result['price']),('腾讯','8.199'))
        fields=['']*32;fields[0]='黄金ETF';fields[3]='8.200';fields[30]='2026-10-23';fields[31]='15:14:00'
        payload=('var hq_str_sh518880="'+','.join(fields)+'";').encode('gb18030')
        def sina_only(request,timeout):
            if 'sinajs' in request.full_url: return io.BytesIO(payload)
            raise OSError('offline')
        with patch('portfolio.urllib.request.urlopen',side_effect=sina_only):
            result=fetch_quote('sh518880',NOW)
        self.assertEqual((result['source'],result['price']),('新浪','8.200'))

    def test_failures_keep_last_success_and_unchanged_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Portfolio(directory);p.configure(CONFIG)
            self.assertTrue(p.accept(QUOTE,NOW))
            before=p.quote.copy(),p.path.read_bytes()
            p.started(NOW+timedelta(minutes=5))
            self.assertFalse(p.accept(None,NOW,error='网络错误'))
            self.assertEqual((p.quote,p.path.read_bytes()),before)
            self.assertFalse(p.accept({**QUOTE,'stamp':'2026-10-22T15:00:00+08:00'},NOW))
            self.assertEqual((p.quote,p.path.read_bytes()),before)
            self.assertEqual(Portfolio(directory).quote,p.quote)
            with patch.object(Path,'write_text',side_effect=OSError('disk full')):
                with self.assertRaises(OSError):p.configure({**CONFIG,'quantity':'1'})
            self.assertEqual((p.quote,p.path.read_bytes()),before)

    def test_empty_and_populated_pages_are_gray16_and_do_not_replace_other_page(self):
        with tempfile.TemporaryDirectory() as directory:
            app=App(DEFAULTS,Path(directory))
            self.assertEqual((render_portfolio(app.portfolio).size,render_portfolio(app.portfolio).mode),((400,300),'L'))
            app.portfolio.configure(CONFIG)
            before=app.preview,app.page,app.revision
            app.portfolio_result((QUOTE,NOW,None))
            self.assertEqual((app.preview,app.page,app.revision),before)
            with patch.object(app,'fetch_portfolio'):
                app.action('portfolio')
            self.assertEqual(app.preview_format,'Gray16')
            self.assertEqual(app.restore_page(),'portfolio')

    def test_does_not_fetch_before_config_or_inside_five_minute_window(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Portfolio(directory)
            self.assertFalse(p.due(NOW))
            p.configure(CONFIG);self.assertTrue(p.due(NOW))
            p.started(NOW);self.assertFalse(p.due(NOW+timedelta(minutes=10)))
            p.accept(QUOTE,NOW)
            self.assertFalse(p.due(NOW+timedelta(seconds=299)))
            self.assertTrue(p.due(NOW+timedelta(seconds=300)))


if __name__=='__main__': unittest.main()
