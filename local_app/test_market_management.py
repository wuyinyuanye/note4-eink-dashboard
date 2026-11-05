import json
import tempfile
import threading
import unittest
import http.client
from datetime import datetime,timedelta
from pathlib import Path
from unittest.mock import patch
from app import App,DEFAULTS,Server,handler
from clock_page import TZ
from market import Market
from menu_page import APPS,render_menu


def row(code='sh518880'):
    return dict(code=code,name='黄金ETF华安',price=8.379,previous=8.412,traded=True,stamp=datetime.now(TZ).isoformat())


class ManagementTests(unittest.TestCase):
    def test_append_sort_delete_retains_quotes_and_disk_failure_rolls_back(self):
        with tempfile.TemporaryDirectory() as d:
            m=Market(d);m.accept([row(c) for c in m.codes],datetime.now(TZ))
            original=m.rows.copy();m.add_quote(row(),datetime.now(TZ))
            self.assertEqual(m.codes[-1],'sh518880');self.assertEqual(m.rows[:3],original)
            self.assertEqual(Market(d).codes,m.codes)
            with self.assertRaises(ValueError):m.add_quote(row(),datetime.now(TZ))
            m.move('sh518880',-1);self.assertEqual(m.codes[2],'sh518880')
            before=m.codes.copy()
            with patch.object(Path,'write_text',side_effect=OSError('disk')):
                with self.assertRaises(OSError):m.remove('sh518880')
            self.assertEqual(m.codes,before)
            m.remove('sh518880');self.assertEqual(len(m.rows),3)
            for c in m.codes.copy()[1:]:m.remove(c)
            with self.assertRaises(ValueError):m.remove(m.codes[0])
            m.fetching=True
            with self.assertRaises(ValueError):m.add_quote(row(),datetime.now(TZ))

    def test_lookup_confirm_add_and_expired_candidate_api(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d));server=Server(('127.0.0.1',0),handler(app,0))
            port=server.server_address[1];server.RequestHandlerClass=handler(app,port)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            def request(path,body):
                conn=http.client.HTTPConnection('127.0.0.1',port,timeout=3)
                try:
                    conn.request('POST','/api/market/'+path,json.dumps(body),{'Content-Type':'application/json'})
                    response=conn.getresponse();return response.status,json.loads(response.read())
                finally:conn.close()
            try:
                self.assertEqual(request('add',{'code':'sh518880'})[0],400)
                with patch('app.fetch_quotes',return_value=[row()]) as fetch:
                    status,result=request('lookup',{'code':'518880'})
                    self.assertEqual(status,200);self.assertEqual(result['quote']['name'],'黄金ETF华安')
                    fetch.assert_called_once_with(['sh518880'])
                self.assertNotIn('sh518880',app.market.codes)
                self.assertEqual(request('add',{'code':'sh518880'})[0],200)
                self.assertIn('sh518880',Market(d).codes)
                self.assertEqual(request('move',{'code':'sh518880','direction':-1})[0],200)
                self.assertEqual(app.market.codes[2],'sh518880')
                self.assertEqual(request('remove',{'code':'sh518880'})[0],200)
                app.market_candidates['sh518880']=(row(),datetime.now(TZ)-timedelta(minutes=3))
                self.assertEqual(request('add',{'code':'sh518880'})[0],400)
                with patch('app.fetch_quotes',side_effect=ValueError('invalid')):
                    self.assertEqual(request('lookup',{'code':'123456'})[0],400)
                self.assertEqual(request('lookup',{'code':'518880 510300'})[0],400)
            finally:server.shutdown();server.server_close();thread.join(timeout=3)

    def test_physical_menu_enters_every_app_and_preserves_recording_gate(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d))
            for index,(page,_,_) in enumerate(APPS):
                app.action('home');app.bridge_event({'event':'key','key':'Down','action':'Long'})
                self.assertEqual(app.page,'menu')
                self.assertEqual(app.preview_format,'Mono')
                for _ in range(index):app.bridge_event({'event':'key','key':'Down','action':'Short'})
                with patch.object(app,'fetch_market'),patch.object(app,'fetch_news'),patch.object(app,'fetch_gold'):
                    app.bridge_event({'event':'key','key':'Confirm','action':'Short'})
                self.assertEqual(app.page,page)
                self.assertEqual(render_menu(index).size,(400,300))
                self.assertEqual(render_menu(index).mode,'L')
            app.action('menu');app.action('down');self.assertEqual(app.menu_index,0)
            app.action('up');self.assertEqual(app.menu_index,len(APPS)-1)
            app.recording=True;app.connection='已连接'
            with patch.object(app,'send') as send:
                app.action('up');send.assert_not_called()
                app.bridge_event({'event':'recording_finished'});send.assert_called_once()
            app.bridge_event({'event':'key','key':'Up','action':'Long'});self.assertEqual(app.page,APPS[-1][0])

    def test_web_navigation_and_both_long_keys_reopen_on_current_app(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d))
            with patch.object(app,'fetch_market'),patch.object(app,'fetch_news'),patch.object(app,'fetch_gold'):
                for page,long_key in (('album','Up'),('market','Down'),('news','Up'),('gold','Up'),('home','Down')):
                    app.action('home')
                    app.action(page)
                    app.bridge_event({'event':'key','key':long_key,'action':'Long'})
                    self.assertEqual(app.page,'menu')
                    self.assertEqual(APPS[app.menu_index][0],page)
                    self.assertEqual(app.preview_format,'Mono')
                    app.action('confirm')
                    self.assertEqual(app.page,page)

    def test_weather_overview_keeps_menu_on_weather_app(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d))
            app.action('details')
            self.assertEqual(app.page,'details')
            app.bridge_event({'event':'key','key':'Down','action':'Long'})
            self.assertEqual(app.page,'menu')
            self.assertEqual(APPS[app.menu_index][0],'home')
            app.action('confirm')
            self.assertEqual(app.page,'details')

    def test_carousel_keeps_focus_and_chrome_fixed(self):
        from PIL import ImageChops
        from menu_page import PANEL
        for mono in (False,True):
            for selected in range(len(APPS)):
                first=render_menu(selected,mono=mono)
                second=render_menu((selected+1)%len(APPS),mono=mono)
                self.assertEqual(first.size,(400,300))
                if mono:self.assertEqual(set(first.tobytes()),{0,255})
                difference=ImageChops.difference(first,second)
                self.assertIsNotNone(difference.getbbox())
                self.assertLess(sum(value!=0 for value in difference.tobytes()),15000)
                # The strip moves while the focus markers and chrome stay fixed.
                difference.paste(0,(320,28,380,47))
                difference.paste(0,(PANEL[0]-3,PANEL[1]-3,PANEL[2]+3,PANEL[3]+3))
                difference.paste(0,(100,234,300,255))
                self.assertIsNone(difference.getbbox())

    def test_same_card_moves_one_pitch_and_transition_reaches_next_state(self):
        from PIL import ImageChops,ImageOps
        from menu_page import CARD_PITCH,CARD_LEFT,CARD_WIDTH,CARD_TOP,CARD_BOTTOM
        for mono in (False,True):
            for selected in range(len(APPS)):
                current=render_menu(selected,mono=mono)
                following=render_menu((selected+1)%len(APPS),mono=mono)
                # Same icon/title moves to the side and changes from inverse
                # white-on-black to normal black-on-white at the fixed focus.
                old=ImageOps.invert(current.crop((CARD_LEFT+12,CARD_TOP+12,CARD_LEFT+CARD_WIDTH-12,CARD_BOTTOM-12)))
                new=following.crop((CARD_LEFT-CARD_PITCH+12,CARD_TOP+12,CARD_LEFT+CARD_WIDTH-CARD_PITCH-12,CARD_BOTTOM-12))
                difference=ImageChops.difference(old,new)
                self.assertLessEqual(difference.getextrema()[1],1)
                for offset in (-1,-.5,0,.5,1):
                    frame=render_menu(selected,mono=mono,offset=offset)
                    for box in ((150,102,250,108),(150,197,250,202)):
                        self.assertIsNone(ImageChops.difference(current.crop(box),frame.crop(box)).getbbox())
                # Whole-strip motion ends at exactly the next/previous state,
                # including news→weather and weather→news wraparound.
                for direction in (-1,1):
                    moved=render_menu(selected,mono=mono,offset=direction)
                    target=render_menu((selected+direction)%len(APPS),mono=mono)
                    self.assertIsNone(ImageChops.difference(moved,target).getbbox())

    def test_menu_is_standalone_even_after_a_gray16_page(self):
        import io
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d))
            for index,(page,_,_) in enumerate(APPS):
                prior=Image.new('L',(400,300),51+index*17)
                stream=io.BytesIO();prior.save(stream,format='PNG')
                app.page=page;app.preview=stream.getvalue();app.preview_format='Gray16'
                app.action('menu')
                actual=Image.open(io.BytesIO(app.preview))
                self.assertEqual(app.preview_format,'Mono')
                self.assertEqual(actual.tobytes(),render_menu(index,mono=True).tobytes())

    def test_long_key_cancels_browsing_and_restores_exact_original_page(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d))
            with patch.object(app,'fetch'),patch.object(app,'fetch_market'),patch.object(app,'fetch_news'),patch.object(app,'fetch_gold'):
                for page in ('home','details','album','market','news','gold'):
                    app.action(page)
                    before=app.preview
                    app.bridge_event({'event':'key','key':'Up','action':'Long'})
                    app.action('down');app.action('down')
                    self.assertEqual(app.page,'menu')
                    app.bridge_event({'event':'key','key':'Down','action':'Long'})
                    self.assertEqual(app.page,page)
                    self.assertEqual(app.preview,before)
                    self.assertIsNone(app.menu_return_page)

    def test_menu_browsing_is_not_navigation_and_state_describes_neighbours(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d));app.action('details');app.action('menu')
            with patch.object(app,'fetch') as weather,patch.object(app,'fetch_market') as market,patch.object(app,'fetch_news') as news:
                for _ in range(len(APPS)+1):app.action('down')
                self.assertEqual(app.page,'menu')
                weather.assert_not_called();market.assert_not_called();news.assert_not_called()
                menu=app.state()['menu']
                self.assertEqual((menu['title'],menu['previous'],menu['next']),('相册','天气','行情'))
                self.assertEqual(menu['return_page'],'details')
                app.action('confirm');self.assertEqual(app.page,'album')
                self.assertIsNone(app.menu_return_page)
                app.action('menu');app.action('news')
                self.assertEqual(app.page,'news');self.assertIsNone(app.menu_return_page)

    def test_background_weather_does_not_repaint_menu_or_advance_album(self):
        with tempfile.TemporaryDirectory() as d:
            app=App(DEFAULTS,Path(d));app.action('menu');app.events.put(('weather',(app.store,True)))
            with patch.object(app,'tick',side_effect=app.stop.set),patch.object(app,'render') as repaint:
                app.run();repaint.assert_not_called()


if __name__=='__main__':unittest.main()
