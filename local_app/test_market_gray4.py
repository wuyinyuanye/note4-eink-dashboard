import io
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock,patch
from PIL import ImageChops
from app import App,DEFAULTS
from market import Market,parse_quotes
from market_page import render_market
from test_market import NOW,quote


class MarketGray4Tests(unittest.TestCase):
    def test_only_numeric_columns_change_in_normal_updates(self):
        with tempfile.TemporaryDirectory() as d:
            m=Market(d);m.configure('sh000001 sz399001 sz399006 sh518880 sh510300 sz159915',300)
            first=''.join(quote(c,price=str(3500+i)) for i,c in enumerate(m.codes))
            m.accept(parse_quotes(first,m.codes,NOW),NOW)
            image=render_market(m,NOW)
            later=NOW+timedelta(minutes=5)
            second=''.join(quote(c,price=str(3505+i),stamp=later) for i,c in enumerate(m.codes))
            m.accept(parse_quotes(second,m.codes,later),later)
            changed=render_market(m,later)
            bounds=ImageChops.difference(image,changed).getbbox()
            self.assertIsNotNone(bounds)
            difference=ImageChops.difference(image,changed)
            difference.paste(0,(194,60,389,256));difference.paste(0,(12,277,330,297))
            self.assertIsNone(difference.getbbox())
            self.assertIsNone(ImageChops.difference(image.crop((0,0,190,260)),changed.crop((0,0,190,260))).getbbox())

    def test_successful_refresh_changes_update_time_even_with_same_prices(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d));app.page='market'
            app.market.accept(parse_quotes(quote(),app.market.codes,NOW),NOW);app.render(now=NOW)
            revision=app.revision
            later=NOW+timedelta(minutes=5)
            app.market.accept(parse_quotes(quote(stamp=later),app.market.codes,later),later)
            with patch.object(app,'dispatch') as send:
                app.render(now=later);send.assert_called_once()
            self.assertEqual(app.revision,revision+1)

    def test_format_tracks_inflight_frame_across_application_switch(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d));app.connection='已连接';app.pending=False
            sent=[];app.send=sent.append
            with patch.object(app,'fetch_market'):app.action('market')
            self.assertEqual(sent[-1]['format'],'Gray4')
            app.action('album');self.assertEqual(len(sent),1)
            app.bridge_event({'event':'displayed','wire_format':'Gray4'})
            self.assertEqual(sent[-1]['format'],'Gray16')
            receipt=__import__('json').loads((Path(d)/'last-display.json').read_text())
            self.assertEqual(receipt['format'],'Gray4');self.assertEqual(receipt['bpp'],2)
            app.bridge_event({'event':'displayed','wire_format':'Gray16'})
            self.assertEqual(app.displayed_format,'Gray16')
            self.assertEqual(app.state()['image_format'],'Gray16')

    def test_initial_connection_also_uses_gray4(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d));app.page='market';app.render(now=NOW)
            process=MagicMock();process.stdout=iter(());process.stderr=io.StringIO('');process.wait.return_value=0
            with patch('app.probe'),patch('app.subprocess.Popen',return_value=process) as spawn:
                app.connect()
            self.assertEqual(spawn.call_args.args[0][-1],'Gray4')
            self.assertEqual(app.inflight_format,'Gray4')

    def test_monochrome_trial_is_market_only_and_exact_black_white(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            app=App({**DEFAULTS,'market_image_format':'Mono'},Path(d))
            app.connection='已连接';app.pending=False;sent=[];app.send=sent.append
            with patch.object(app,'fetch_market'):app.action('market')
            self.assertEqual(sent[-1]['format'],'Mono')
            image=Image.open(io.BytesIO(app.preview))
            self.assertTrue(set(image.tobytes())<={0,255})
            app.bridge_event({'event':'displayed','wire_format':'Mono'})
            receipt=__import__('json').loads((Path(d)/'last-display.json').read_text())
            self.assertEqual(receipt['bpp'],1)
            app.action('home');self.assertEqual(sent[-1]['format'],'Gray16')
            with self.assertRaises(ValueError):App({**DEFAULTS,'market_image_format':'bad'},Path(d))

    def test_native_mono_small_labels_survive_and_only_numbers_change(self):
        with tempfile.TemporaryDirectory() as d:
            m=Market(d);m.configure('sh000001 sz399001 sz399006 sh518880 sh510300 sz159915',300)
            m.accept(parse_quotes(''.join(quote(c) for c in m.codes),m.codes,NOW),NOW)
            first=render_market(m,NOW,mono=True)
            self.assertTrue(set(first.tobytes())<={0,255})
            # These labels previously became a handful of scattered black pixels.
            self.assertGreater(first.crop((12,41,120,55)).tobytes().count(0),100)
            self.assertGreater(first.crop((12,279,275,296)).tobytes().count(0),100)
            later=NOW+timedelta(minutes=5)
            m.accept(parse_quotes(''.join(quote(c,price='3510',stamp=later) for c in m.codes),m.codes,later),later)
            second=render_market(m,later,mono=True)
            difference=ImageChops.difference(first,second)
            difference.paste(0,(194,60,389,256));difference.paste(0,(12,277,330,297))
            self.assertIsNone(difference.getbbox())
            self.assertIsNone(ImageChops.difference(first.crop((0,0,190,260)),second.crop((0,0,190,260))).getbbox())

    def test_market_uses_zfull_for_all_text_without_system_font_dependency(self):
        from market_page import market_font
        self.assertIn('Zfull',market_font(12).getname()[0])
        with tempfile.TemporaryDirectory() as d,patch('clock_page.font_path',side_effect=RuntimeError('system fonts unavailable')):
            m=Market(d)
            for mono in (False,True):self.assertEqual(render_market(m,NOW,mono=mono).size,(400,300))

    def test_physical_confirm_starts_fetch_and_records_key(self):
        from datetime import datetime
        from clock_page import TZ
        now=datetime.now(TZ)
        with tempfile.TemporaryDirectory() as d:
            app=App({**DEFAULTS,'market_image_format':'Mono'},Path(d));app.page='market'
            rows=parse_quotes(quote(stamp=now),app.market.codes,now)
            app.market.accept(rows,now-timedelta(minutes=1));app.render(now=now)
            app.connection='已连接';app.pending=False
            with patch('app.fetch_quotes',return_value=rows) as fetch,patch.object(app,'send') as send:
                app.bridge_event({'event':'key','key':'Confirm','action':'Short'})
                event,value=app.events.get(timeout=3);self.assertEqual(event,'market')
                app.market_result(value)
                fetch.assert_called_once();send.assert_called_once()
            self.assertEqual(app.last_key['key'],'Confirm')
            self.assertEqual(app.market.last_refresh_trigger,'confirm')


if __name__=='__main__':unittest.main()
