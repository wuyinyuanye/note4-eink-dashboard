import tempfile, unittest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch
from PIL import ImageChops
from clock_page import TZ
from sectors import Sectors, SECTORS, CODES
from sectors_page import render_sectors
from app import App, DEFAULTS

NOW=datetime(2026,11,5,10,10,tzinfo=TZ)
def rows(now=NOW):
    return [dict(code=code,name=name,price=10.1,previous=10.,traded=True,stamp=now.isoformat()) for name,code in SECTORS]

class SectorTests(unittest.TestCase):
    def test_complete_binary_fixed_and_reject_failed_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=Sectors(Path(tmp)); self.assertTrue(m.accept(rows(),NOW))
            before=render_sectors(m); self.assertEqual(set(before.getdata()),{0,255})
            self.assertFalse(m.accept(rows()[:-1],NOW));self.assertEqual(before.tobytes(),render_sectors(m).tobytes())
            self.assertFalse(m.accept(rows(NOW-timedelta(minutes=11)),NOW));self.assertEqual(before.tobytes(),render_sectors(m).tobytes())
            later=rows();later[4]['price']=10.2
            self.assertTrue(m.accept(later,NOW)); box=ImageChops.difference(before,render_sectors(m)).getbbox()
            self.assertIsNotNone(box);self.assertGreaterEqual(box[0],114);self.assertLessEqual(box[2],193)
            self.assertEqual(Sectors(Path(tmp)).codes,CODES)
    def test_app_selects_mono_and_restores_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            app=App(DEFAULTS,Path(tmp))
            with patch.object(app,'fetch_sectors'):
                app.action('sectors')
            self.assertEqual(app.image_format(),'Mono');self.assertEqual(app.restore_page(),'sectors')
            self.assertEqual(set(__import__('PIL').Image.open(Path(tmp)/'preview.png').getdata()),{0,255})
            self.assertEqual(app.state()['page'],'sectors')
