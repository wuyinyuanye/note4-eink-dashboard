import io
import json
import os
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from album import Album, is_landscape_image, local_photo_bytes, local_photo_candidates
from app import App,DEFAULTS
from test_weather_app import NOW

def photo(color=100,size=(600,900)):
    output=io.BytesIO();Image.new('RGB',size,(color,color,color)).save(output,format='PNG');return output.getvalue()

class AlbumTests(unittest.TestCase):
    def test_upload_orientation_alpha_duplicate_and_persistence(self):
        with tempfile.TemporaryDirectory() as directory:
            album=Album(directory);data=photo()
            album.add(data,'../人物.png');album.add(data,'same.png')
            self.assertEqual(len(album.photos),1);self.assertEqual(album.photos[0]['name'],'人物.png')
            self.assertEqual(album.render().size,(400,300));self.assertEqual(album.render().mode,'L')
            album.configure('cover',60);self.assertEqual(album.render().getpixel((0,0)),100)
            restored=Album(directory);self.assertEqual(len(restored.photos),1);self.assertEqual(restored.fit,'cover')
            self.assertFalse(restored.playing)
            transparent=io.BytesIO();Image.new('RGBA',(20,40),(0,0,0,0)).save(transparent,format='PNG')
            album.select(album.add(transparent.getvalue(),'transparent.png'))
            self.assertEqual(album.render().getpixel((200,150)),255)
            rotated=io.BytesIO();exif=Image.Exif();exif[274]=6
            Image.new('RGB',(20,40),'white').save(rotated,format='JPEG',exif=exif)
            index=album.add(rotated.getvalue(),'rotated.jpg')
            self.assertEqual((album.photos[index]['width'],album.photos[index]['height']),(40,20))
            with self.assertRaises(ValueError): album.add(b'not image','bad')
            with self.assertRaises(ValueError): album.path('../config')

    def test_delete_preserves_selected_and_empty_library(self):
        with tempfile.TemporaryDirectory() as directory:
            album=Album(directory)
            for color in (50,100,150): album.add(photo(color),str(color))
            album.select(1);selected=album.photos[1]['id'];album.delete(album.photos[0]['id'])
            self.assertEqual(album.photos[album.current]['id'],selected)
            for item in list(album.photos): album.delete(item['id'])
            self.assertEqual(album.current,0);self.assertEqual(album.render().size,(400,300))

    def test_remote_cache_date_and_capacity(self):
        with tempfile.TemporaryDirectory() as directory:
            album=Album(directory);album.set_source('http://192.0.2.50:8080/')
            for color in range(52):
                album.cache_remote(photo(color,(40,60)),{'id':str(color),'datetime_original':'2022-10-21 12:00:00','_next_offset':color+1})
            self.assertEqual(len(album.photos),48);self.assertEqual(len(list(album.root.glob('*.png'))),48)
            self.assertEqual(album.photos[-1]['capture_date'],'2022-10-21')
            self.assertEqual(album.remote_offset,52)
            with self.assertRaises(ValueError): album.set_source('file:///tmp')

    def test_navigation_slideshow_and_busy_gates(self):
        with tempfile.TemporaryDirectory() as directory:
            app=App(DEFAULTS,Path(directory))
            for color in (50,100): app.album.add(photo(color),str(color))
            app.bridge_event({'event':'key','key':'Down','action':'Long'})
            self.assertEqual(app.page,'menu')
            app.action('down');app.action('confirm')
            self.assertEqual(app.page,'album')
            app.action('down');self.assertEqual(app.album.current,1)
            app.action('confirm');self.assertTrue(app.album.playing)
            app.album.interval=60;app.last_slide=0;app.last_fetch=time.monotonic();app.connection='已连接'
            for attribute,value in (('recording',True),('blocked',True),('inflight',4)):
                setattr(app,attribute,value)
                with patch.object(app,'fetch'): app.tick(NOW,100)
                self.assertEqual(app.album.current,1)
                setattr(app,attribute,False if attribute!='inflight' else None)
            app.album.source='http://127.0.0.1:8080'
            with patch.object(app,'dispatch'),patch.object(app,'fetch'),patch.object(app,'sync_album') as sync:
                app.tick(NOW,100)
                sync.assert_not_called()
            self.assertEqual(app.album.current,0)
            app.album.save()
            self.assertTrue(Album(Path(directory)).playing)
            app.action('home');self.assertEqual(app.page,'home')
            with patch.object(app,'fetch'): app.tick(NOW,200)
            self.assertEqual(app.album.current,0)

    def test_daily_library_selects_unused_landscape_and_persists_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);photos=root/'photos';photos.mkdir()
            for name,size in (('first.jpg',(900,600)),('portrait.jpg',(600,900)),('second.jpg',(1200,800))):
                Image.new('RGB',size,'white').save(photos/name,format='JPEG')
            index=root/'photo_index.json'
            index.write_text(json.dumps({'photos':[
                {'id':'first','path':'first.jpg','w':900,'h':600,'datetime_original':'2020-01-01 10:00:00'},
                {'id':'portrait','path':'portrait.jpg','w':600,'h':900},
                {'id':'second','path':'second.jpg','w':1200,'h':800},
            ]}))
            album=Album(root/'data',index,photos)
            self.assertTrue(album.local_library_available)
            self.assertTrue(album.needs_daily(date(2026,11,5)))
            candidates=local_photo_candidates(index,photos,exclude_ids=('first',),seed='2026-11-05')
            self.assertEqual([item['id'] for item in candidates],['second'])
            data,name=local_photo_bytes(photos,candidates[0])
            self.assertTrue(is_landscape_image(data))
            photo={**candidates[0],'name':name,'source':'NAS相册'}
            selected=album.cache_remote(data,photo,landscape_only=True)
            album.complete_daily(date(2026,11,5),photo['id'],selected,photo['source'])
            self.assertEqual((album.daily_date,album.daily_photo_id,album.current),('2026-11-05','second',selected))
            self.assertFalse(album.needs_daily(date(2026,11,5)))
            self.assertTrue(album.needs_daily(date(2026,11,6)))
            self.assertEqual(album.render().size,(400,300))
            restored=Album(root/'data',index,photos)
            self.assertEqual((restored.daily_date,restored.daily_photo_id),('2026-11-05','second'))
            self.assertEqual(restored.fit,'cover')

    def test_daily_index_rejects_traversal_and_non_landscape_orientation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);photos=root/'photos';photos.mkdir()
            outside=io.BytesIO();Image.new('RGB',(20,10),'white').save(outside,format='PNG')
            (root/'outside.png').write_bytes(outside.getvalue())
            with self.assertRaises(ValueError):
                local_photo_bytes(photos,{'path':'../outside.png'})
            portrait=io.BytesIO();Image.new('RGB',(20,40),'white').save(portrait,format='PNG')
            self.assertFalse(is_landscape_image(portrait.getvalue()))
            rotated=io.BytesIO();exif=Image.Exif();exif[274]=6
            Image.new('RGB',(20,40),'white').save(rotated,format='JPEG',exif=exif)
            self.assertTrue(is_landscape_image(rotated.getvalue()))

    def test_local_daily_source_triggers_once_per_day(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);photos=root/'photos';photos.mkdir()
            Image.new('RGB',(900,600),'white').save(photos/'daily.jpg',format='JPEG')
            index=root/'photo_index.json'
            index.write_text(json.dumps({'photos':[{'id':'daily','path':'daily.jpg','w':900,'h':600}]}))
            environment={'NOTE4_PHOTO_INDEX':str(index),'NOTE4_PHOTO_ROOT':str(photos)}
            with patch.dict(os.environ,environment):
                app=App(DEFAULTS,root/'data')
            self.assertTrue(app.album.local_library_available)
            with patch.object(app,'sync_album') as sync,patch.object(app,'fetch'),patch.object(app.album,'needs_daily',side_effect=[True,False]) as needs_daily:
                app.tick(NOW,100)
                sync.assert_called_once_with()
                app.tick(NOW,200)
                sync.assert_called_once_with()
                self.assertEqual(needs_daily.call_count,2)

if __name__=='__main__': unittest.main()
