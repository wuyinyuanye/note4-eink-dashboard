"""Persistent local photo library with one landscape remote photo per day."""
import hashlib
import io
import json
import warnings
from datetime import date
from urllib.request import urlopen, Request
from urllib.parse import urlsplit, quote
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError

MAX_UPLOAD=20*1024*1024
MAX_INDEX=32*1024*1024
DAILY_HISTORY_LIMIT=365
LOCAL_EXTENSIONS={'.jpg','.jpeg','.png','.webp','.bmp','.tif','.tiff'}

class Album:
    def __init__(self, directory, photo_index=None, photo_root=None):
        self.root=Path(directory)/'album';self.root.mkdir(parents=True,exist_ok=True)
        self.photo_index=Path(photo_index) if photo_index else None
        self.photo_root=Path(photo_root) if photo_root else None
        self.photos=[];self.current=0;self.fit='cover';self.interval=300;self.playing=False;self.source='';self.date_label=True;self.remote_offset=0
        self.daily_date=None;self.daily_photo_id=None;self.daily_source=None;self.daily_used_ids=[]
        try:
            saved=json.loads((self.root/'library.json').read_text())
            self.photos=[p for p in saved['photos'] if self.path(p['id']).is_file()]
            self.remote_offset=max(0,int(saved.get('remote_offset',0)));self.source=saved.get('source','');self.date_label=saved.get('date_label',True)
            self.current=int(saved.get('current',0))%max(1,len(self.photos))
            self.fit='cover'
            self.interval=min(86400,max(60,int(saved.get('interval',300))))
            self.playing=saved.get('playing') is True and bool(self.photos)
            self.daily_date=saved.get('daily_date') if isinstance(saved.get('daily_date'),str) else None
            self.daily_photo_id=str(saved['daily_photo_id']) if saved.get('daily_photo_id') is not None else None
            self.daily_source=saved.get('daily_source') if isinstance(saved.get('daily_source'),str) else None
            used=saved.get('daily_used_ids',[])
            if isinstance(used,list):
                self.daily_used_ids=[str(value) for value in used if isinstance(value,(str,int))][-DAILY_HISTORY_LIMIT:]
        except (OSError,ValueError,KeyError,TypeError): pass

    @property
    def local_library_available(self):
        return bool(self.photo_index and self.photo_root and self.photo_index.is_file() and self.photo_root.is_dir())

    def needs_daily(self, today=None):
        if not self.local_library_available: return False
        today=(today or date.today()).isoformat()
        if self.daily_date!=today or not self.daily_photo_id: return True
        return not any(p.get('remote_id')==self.daily_photo_id for p in self.photos)

    def path(self, photo_id):
        if not isinstance(photo_id,str) or len(photo_id)!=64 or any(c not in '0123456789abcdef' for c in photo_id):
            raise ValueError('照片编号无效')
        return self.root/(photo_id+'.png')

    def save(self):
        temp=self.root/'library.tmp'
        temp.write_text(json.dumps(self.state(),ensure_ascii=False));temp.replace(self.root/'library.json')

    def state(self):
        return {'remote_offset':self.remote_offset,'source':self.source,'date_label':self.date_label,'photos':[dict(p) for p in self.photos],'current':self.current,'fit':self.fit,'interval':self.interval,'playing':self.playing,
                'daily_date':self.daily_date,'daily_photo_id':self.daily_photo_id,'daily_source':self.daily_source,'daily_available':self.local_library_available}

    def add(self, data, name, metadata=None, landscape_only=False):
        if not data or len(data)>MAX_UPLOAD: raise ValueError('每张照片最多20MB')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error',Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(data)) as source:
                    if source.format not in ('JPEG','PNG','WEBP','BMP','TIFF'): raise ValueError('请上传 JPG、PNG、WebP、BMP 或 TIFF 图片')
                    source.load();image=ImageOps.exif_transpose(source).convert('RGBA')
                    if landscape_only and image.width<=image.height: raise ValueError('照片不是横屏')
                    image.thumbnail((2400,2400),Image.Resampling.LANCZOS)
                    background=Image.new('RGBA',image.size,'white');background.alpha_composite(image)
                    image=background.convert('RGB')
        except (UnidentifiedImageError,OSError,Image.DecompressionBombWarning,Image.DecompressionBombError) as exc:
            raise ValueError('图片无法读取或尺寸过大') from exc
        output=io.BytesIO();image.save(output,format='PNG');png=output.getvalue()
        photo_id=hashlib.sha256(png).hexdigest()
        for index,p in enumerate(self.photos):
            if p['id']==photo_id:
                if metadata: p.update(metadata);self.save()
                return index
        self.path(photo_id).write_bytes(png)
        self.photos.append({'id':photo_id,'name':Path(name.replace('\\','/')).name[:120] or '未命名照片','width':image.width,'height':image.height,**(metadata or {})})
        self.save();return len(self.photos)-1

    def select(self,index):
        if type(index) is not int or not 0<=index<len(self.photos): raise ValueError('照片不存在')
        self.current=index;self.save()

    def move(self,step):
        if self.photos: self.select((self.current+step)%len(self.photos))

    def delete(self,photo_id):
        path=self.path(photo_id)
        index=next((i for i,p in enumerate(self.photos) if p['id']==photo_id),None)
        if index is None: raise ValueError('照片不存在')
        selected=self.photos[self.current]['id']
        self.photos.pop(index)
        self.current=next((i for i,p in enumerate(self.photos) if p['id']==selected),min(index,max(0,len(self.photos)-1)))
        if not self.photos: self.playing=False
        self.save();path.unlink(missing_ok=True)

    def configure(self,fit,interval):
        if fit!='cover' or type(interval) is not int or not 60<=interval<=86400:
            raise ValueError('相册固定横屏铺满，轮播间隔应为60–86400秒')
        self.fit='cover';self.interval=interval;self.save()

    def render(self):
        if not self.photos:
            image=Image.new('L',(400,300),255);draw=ImageDraw.Draw(image)
            draw.rectangle((150,75,250,175),outline=140,width=2)
            draw.ellipse((169,92,186,109),fill=150)
            draw.line((156,165,190,128,216,151,235,132,245,165),fill=100,width=3)
            draw.text((137,205),'Upload photos on Mac',fill=70)
            return image
        with Image.open(self.path(self.photos[self.current]['id'])) as source:
            image=source.convert('L')
            return self.label(ImageOps.fit(image,(400,300),Image.Resampling.LANCZOS,centering=(.5,.5)))

    def label(self,image):
        capture=self.photos[self.current].get('capture_date')
        if self.date_label and capture:
            draw=ImageDraw.Draw(image);font=ImageFont.load_default(size=15)
            box=draw.textbbox((0,0),capture,font=font)
            width=box[2]-box[0];draw.rectangle((390-width-10,270,399,299),fill=0)
            draw.text((390-width-5,276-box[1]),capture,font=font,fill=255)
        return image

    def set_source(self,source):
        if not isinstance(source,str): raise ValueError('服务地址必须是文字')
        parsed=urlsplit(source)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ('','/'):
            raise ValueError('请填写电子相框服务地址，如 http://192.0.2.50:8080')
        normal=source.rstrip('/')
        if normal!=self.source: self.remote_offset=0
        self.source=normal;self.save()

    def cache_remote(self,data,photo,landscape_only=False):
        capture=None
        if photo.get('shot_time_source')=='exif' or photo.get('datetime_original'):
            try: capture=date.fromisoformat(str(photo.get('datetime_original') or photo.get('shot_time_text'))[:10]).isoformat()
            except (ValueError,TypeError): pass
        self.remote_offset=photo.get('_next_offset',self.remote_offset)
        remote_id=str(photo['id'])
        index=self.add(data,photo.get('name','相框照片'),{'capture_date':capture,'remote_id':remote_id,'source':photo.get('source') or self.source},landscape_only=landscape_only)
        # Bound the persistent remote cache; manual uploads are retained.
        while sum(bool(p.get('remote_id')) for p in self.photos)>48:
            old=next(p for i,p in enumerate(self.photos) if p.get('remote_id') and i!=self.current)
            self.delete(old['id'])
        index=next(i for i,p in enumerate(self.photos) if p.get('remote_id')==remote_id)
        self.save()
        return index

    def complete_daily(self, selected_date, remote_id, photo_index, source):
        self.daily_date=selected_date.isoformat()
        self.daily_photo_id=str(remote_id)
        self.daily_source=source
        if str(remote_id) not in self.daily_used_ids: self.daily_used_ids.append(str(remote_id))
        self.daily_used_ids=self.daily_used_ids[-DAILY_HISTORY_LIMIT:]
        self.current=photo_index;self.fit='cover';self.save()


def local_photo_candidates(index_path, photo_root, exclude_ids=(), seed=''):
    """Read a mounted photo index and return deterministic, unused landscape candidates."""
    path=Path(index_path)
    root=Path(photo_root)
    if not path.is_file() or not root.is_dir(): raise ValueError('照片索引不可用')
    if path.stat().st_size>MAX_INDEX: raise ValueError('照片索引过大')
    payload=json.loads(path.read_text(encoding='utf-8'))
    photos=payload.get('photos') if isinstance(payload,dict) else None
    if not isinstance(photos,list): raise ValueError('照片索引格式无效')
    excluded={str(value) for value in exclude_ids}
    candidates=[]
    for photo in photos:
        if not isinstance(photo,dict) or photo.get('id') is None: continue
        remote_id=str(photo['id'])
        width,height=photo.get('w'),photo.get('h')
        relative=photo.get('path')
        if remote_id in excluded or type(width) is not int or type(height) is not int or width<=height: continue
        if not isinstance(relative,str) or Path(relative.replace('\\','/')).suffix.lower() not in LOCAL_EXTENSIONS: continue
        candidates.append({**photo,'id':remote_id,'w':width,'h':height,'source':'照片目录'})
    # Stable daily choice without biasing toward the most recent portion of the library.
    candidates.sort(key=lambda photo: hashlib.sha256(f'{seed}:{photo["id"]}'.encode()).digest())
    return candidates


def local_photo_bytes(photo_root, photo):
    """Resolve one index entry under the read-only photo root and enforce size limits."""
    root=Path(photo_root).resolve()
    relative=str(photo.get('path','')).replace('\\','/')
    if not relative or relative.startswith('/') or '..' in Path(relative).parts:
        raise ValueError('照片路径无效')
    path=(root/relative).resolve()
    try: path.relative_to(root)
    except ValueError: raise ValueError('照片路径无效') from None
    if not path.is_file(): raise ValueError('照片已不存在')
    if path.stat().st_size>MAX_UPLOAD: raise ValueError('照片超过20MB')
    return path.read_bytes(), path.name


def is_landscape_image(data):
    """Validate the source orientation after applying EXIF rotation."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error',Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as source:
                image=ImageOps.exif_transpose(source)
                return image.width>image.height
    except (UnidentifiedImageError,OSError,Image.DecompressionBombWarning,Image.DecompressionBombError):
        return False


def remote_photos(source,limit=12,offset=0):
    """Read the existing frame service only; never scan or modify its library."""
    def get(path,maximum):
        with urlopen(Request(source+path,headers={'User-Agent':'Note4Album/1.0'}),timeout=20) as response:
            data=response.read(maximum+1)
            if len(data)>maximum: raise ValueError('相框服务响应超过大小限制')
            return data
    listing=json.loads(get('/api/photos?limit='+str(limit)+'&offset='+str(offset),2*1024*1024))
    if not listing.get('photos') and offset:
        offset=0;listing=json.loads(get('/api/photos?limit='+str(limit),2*1024*1024))
    photos=listing.get('photos')
    if not isinstance(photos,list): raise ValueError('相框照片列表格式无效')
    for index,photo in enumerate(photos[:limit]):
        if not isinstance(photo,dict) or not isinstance(photo.get('id'),(str,int)): continue
        width,height=photo.get('w',photo.get('width')),photo.get('h',photo.get('height'))
        if type(width) is int and type(height) is int and width<=height: continue
        data=get('/api/photo/'+quote(str(photo['id']),safe=''),MAX_UPLOAD)
        photo['_next_offset']=offset+index+1
        photo.setdefault('source','电子相框')
        yield data,photo
