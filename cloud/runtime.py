"""Stateless WSGI adapter retaining account, ownership and contact rules."""
import io
import os
import threading
import ipaddress
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import urlsplit,parse_qs,quote
from flask import Flask, request, Response
from werkzeug.datastructures import Headers
import huanchang_server as hc
from hc_readiness import SMTPDelivery
from cloud.database import connect
from cloud.storage import Storage

class CloudHandler(hc.Handler):
    @contextmanager
    def db(self):
        with self.server.database() as db:
            previous=getattr(self,'_active_db',None);self._active_db=db
            try:yield db
            finally:self._active_db=previous
    def read(self,raw=False):
        value=super().read(raw)
        def validate(item):
            if isinstance(item,str):hc.check(not any(c=='\0' or 0xD800<=ord(c)<=0xDFFF for c in item),'内容包含无法保存的字符，请修改后重试。')
            elif isinstance(item,list):
                for child in item:validate(child)
            elif isinstance(item,dict):
                for key,child in item.items():validate(key);validate(child)
        if not raw:validate(value)
        return value
    def rate(self, key, limit=60, scope=None):
        scope = hc.digest(scope or self.client_address[0]);window = hc.now() // 60000
        def increment(db):
            db.execute('DELETE FROM rate_windows WHERE window_id<?', (window - 2,))
            return db.execute('INSERT INTO rate_windows VALUES(?,?,?,1) ON CONFLICT(scope,kind,window_id) DO UPDATE SET total=rate_windows.total+1 RETURNING total', (scope,key,window)).fetchone()[0]
        active=getattr(self,'_active_db',None)
        if active:total=increment(active)
        else:
            with self.db() as db:total=increment(db)
        hc.check(total <= limit, '操作太频繁，请稍后再试。', 429)
    def state(self, db, session):
        value = super().state(db,session);value['uploadMode'] = 'signed';return value
    def change(self, db, uid, path, payload, session):
        if path == '/api/upload/prepare':
            mime = payload.get('mime');size = payload.get('size')
            hc.check(mime in ['image/jpeg','image/png','image/webp'], '请选择 JPG、PNG 或 WebP 图片。')
            hc.check(type(size) is int and 0<size<=20*1024*1024, '单张图片请控制在 20 MB 以内。', 413)
            name = hc.text(payload,'name',160,True);stamp = hc.now()
            used = db.execute('SELECT COALESCE(sum(size),0) FROM uploads WHERE owner=?',(uid,)).fetchone()[0]
            pending = db.execute('SELECT COALESCE(sum(size),0) FROM upload_reservations WHERE owner=? AND expires>?',(uid,stamp)).fetchone()[0]
            hc.check(used+pending+size<=500*1024*1024, '当前账户图片空间已达 500 MB，请联系维护者。', 413)
            fid = hc.ident();url = self.server.storage.signed(fid,upload=True)
            db.execute('INSERT INTO upload_reservations VALUES(?,?,?,?,?,?)',(fid,uid,mime,name,size,stamp+2*3600000))
            return {'id':fid,'uploadUrl':url}
        if path == '/api/upload/complete':
            fid = hc.text(payload,'id',24,True)
            old = db.execute('SELECT * FROM uploads WHERE id=? AND owner=?',(fid,uid)).fetchone()
            if old:return self.upload_result(old)
            reserved = db.execute('SELECT * FROM upload_reservations WHERE id=? AND owner=? AND expires>?',(fid,uid,hc.now())).fetchone()
            hc.check(reserved, '上传授权已过期或不属于此账户，请重新上传。', 403)
            head,size = self.server.storage.inspect(fid);mime = reserved['mime']
            valid = mime=='image/png' and head.startswith(b'\x89PNG\r\n\x1a\n') or mime=='image/jpeg' and head.startswith(b'\xff\xd8\xff') or mime=='image/webp' and head.startswith(b'RIFF') and head[8:12]==b'WEBP'
            hc.check(valid and size==reserved['size'], '图片内容或大小与上传信息不一致，请重新上传。')
            used = db.execute('SELECT COALESCE(sum(size),0) FROM uploads WHERE owner=?',(uid,)).fetchone()[0]
            hc.check(used+size<=500*1024*1024, '当前账户图片空间已达上限。', 413)
            db.execute('INSERT INTO uploads VALUES(?,?,?,?,?,?)',(fid,uid,mime,reserved['name'],size,hc.now()))
            db.execute('DELETE FROM upload_reservations WHERE id=?',(fid,))
            return self.upload_result(reserved)
        return super().change(db,uid,path,payload,session)
    def upload_result(self,row):
        return {'id':row['id'],'name':row['name'],'size':row['size'],'url':'/api/media/'+row['id']}
    def upload(self,db,uid):raise hc.Problem('请刷新页面后使用新版图片上传。')
    def file(self,file,mime):
        if file.parent==self.server.media_path:
            self.send_response(302);self.send_header('Location',self.server.storage.signed(file.name));self.send_header('Cache-Control','private, no-store');self.end_headers();return
        return super().file(file,mime)
    def send_response(self,status,message=None):self.response_status=status
    def send_header(self,name,value):self.response_headers.add(name,value)
    def end_headers(self):pass
    def send_error(self,status,message=None,explain=None):self.output({'error':'内容暂时无法访问。'},status)

class MediaPath:
    def __init__(self,name='',parent=None):self.name=name;self.parent=parent
    def __truediv__(self,name):return MediaPath(name,self)
    def is_file(self):return True

def create_app(database=None,storage=None,origin=None,mail_sender=None,testing=False):
    app=Flask(__name__,static_folder=None);app.config['MAX_CONTENT_LENGTH']=256000
    @app.errorhandler(413)
    def too_large(error):return {'error':'提交内容太大，请刷新页面后使用授权图片上传。'},413
    if origin is None:
        preview=os.getenv('VERCEL_URL','') if os.getenv('VERCEL_ENV')=='preview' else ''
        origin='https://'+preview if preview else os.getenv('HUANCHANG_PUBLIC_ORIGIN','')
    parsed=urlsplit(origin);runtime=None
    try:
        if not (parsed.scheme==('http' if testing else 'https') and parsed.hostname and not parsed.username and parsed.path in ['','/'] and not parsed.query and not parsed.fragment):raise ValueError('Public origin missing')
        if database is None:
            url=os.getenv('HUANCHANG_DATABASE_URL','');dsn=urlsplit(url)
            mode=parse_qs(dsn.query).get('sslmode',[''])[-1]
            if dsn.scheme not in ['postgres','postgresql'] or not dsn.hostname or mode not in ['require','verify-full']:raise ValueError('Database TLS missing')
            password=os.getenv('HUANCHANG_DATABASE_PASSWORD','')
            if password:
                if not dsn.username:raise ValueError('Database user missing')
                host=dsn.netloc.rsplit('@',1)[-1]
                url=dsn._replace(netloc=dsn.username+':'+quote(password,safe='')+'@'+host).geturl()
            database=lambda:connect(url)
        storage=storage or Storage(os.getenv('HUANCHANG_SUPABASE_URL',''),os.getenv('HUANCHANG_SUPABASE_SERVICE_KEY',''),os.getenv('HUANCHANG_STORAGE_BUCKET','huanchang-media'))
        sender=mail_sender or SMTPDelivery()
        runtime=SimpleNamespace(database=database,storage=storage,media_path=MediaPath(),demo=False,public_origin=origin.rstrip('/'),cookie_name='__Host-huanchang_session' if not testing else 'huanchang_cloud_test',secure_cookie=not testing,mail_sender=sender,mail_enabled=bool(mail_sender or sender.enabled),mail_slots=threading.BoundedSemaphore(8),mail_failures=0,mail_dispatch=lambda job:job())
    except ValueError:pass
    @app.route('/',defaults={'path':''},methods=['GET','POST','PATCH','DELETE'])
    @app.route('/<path:path>',methods=['GET','POST','PATCH','DELETE'])
    def handle(path):
        if runtime is None:return {'error':'站点尚未完成云端配置，请联系维护者。'},503
        handler=CloudHandler.__new__(CloudHandler);handler.server=runtime;handler.path=request.full_path.rstrip('?');handler.command=request.method
        address=request.remote_addr or 'unknown'
        if not testing and os.getenv('VERCEL')=='1':
            try:address=str(ipaddress.ip_address(request.headers.get('X-Vercel-Forwarded-For','')))
            except ValueError:pass
        handler.headers=request.headers;handler.client_address=(address,0);handler.rfile=io.BytesIO(request.get_data());handler.wfile=io.BytesIO();handler.response_status=200;handler.response_headers=Headers()
        try:
            if request.method=='GET':handler.do_GET()
            else:handler.api()
        except Exception:
            handler.wfile=io.BytesIO();handler.response_headers=Headers();handler.output({'error':'服务暂时无法访问，请稍后重试。'},503)
        response=Response(handler.wfile.getvalue(),status=handler.response_status,headers=handler.response_headers)
        if not path.startswith('api/') and path!='healthz':
            storage_origin=getattr(storage,'origin','');csp=response.headers.get('Content-Security-Policy','')
            if storage_origin and csp:response.headers['Content-Security-Policy']=csp.replace("img-src 'self'","img-src 'self' "+storage_origin).replace("connect-src 'self'","connect-src 'self' "+storage_origin)
        return response
    app.extensions['huanchang_runtime']=runtime
    return app
