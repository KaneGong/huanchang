#!/usr/bin/env python3
"""Huanchang foundation. Local by default; no demo auth unless --demo explicitly set."""
import argparse
import hashlib
import hmac
import json
import math
import mimetypes
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager, closing
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, unquote
from hc_readiness import client_ip, proxy_networks, params, work_page, SMTPDelivery

ROOT = Path(__file__).resolve().parent
WEB = ROOT / 'outputs/huanchang-v1'
FORMS = ['图像', '视频', '声音', '文字', '互动', '3D']
MODES = ['共同创作', '有偿委托', '先交流再确定']
RIGHTS = ['未说明，使用前请联系作者', '仅供欣赏', '可交流授权']

def dump(x): return json.dumps(x, ensure_ascii=False)
def now(): return int(time.time() * 1000)
def ident(): return secrets.token_hex(12)
def digest(x): return hashlib.sha256(x.encode()).hexdigest()
def password_hash(password, salt):
    return hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()

class Problem(Exception):
    def __init__(self, message, status=400): self.message, self.status = message, status

def check(ok, message, status=400):
    if not ok: raise Problem(message, status)

def text(data, key, limit, required=False):
    v = data.get(key, '')
    check(isinstance(v, str), '填写内容格式不正确。')
    v = v.strip()
    label={'title':'标题','body':'正文','role':'本人贡献','process':'创作过程','tools':'AI 参与说明','lookingFor':'交流方向','topic':'主题','url':'作品链接','name':'显示名称','bio':'一句话介绍','intro':'个人介绍','location':'所在地','handle':'账号名','email':'邮箱','code':'恢复码','key':'草稿标识','reason':'原因','note':'处理说明'}.get(key,'填写内容')
    check(len(v) <= limit and (v or not required), f'请检查{label}，内容为空或超过 {limit} 字。')
    return v

def public_profile(row):
    p = json.loads(row['data']); p.update(id=row['id'], handle=row['handle'], demo=bool(row['demo']))
    p.pop('avatarEdit',None);p.pop('bannerEdit',None);p.pop('email',None)
    p['revision']=p.get('revision',1);p['initial'] = p.get('name', '新')[:1]; p['color'] = '#e8e7dc'
    return p

def initialize(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as db, db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,handle TEXT UNIQUE,password TEXT,salt TEXT,recovery TEXT,data TEXT NOT NULL,role TEXT DEFAULT 'member',disabled INTEGER DEFAULT 0,demo INTEGER DEFAULT 0,email TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,actor TEXT,csrf TEXT,expires INTEGER);
        CREATE TABLE IF NOT EXISTS works(id TEXT PRIMARY KEY,author TEXT,data TEXT NOT NULL,deleted INTEGER DEFAULT 0,hidden INTEGER DEFAULT 0,created INTEGER,updated INTEGER,revision INTEGER DEFAULT 1);
        CREATE TABLE IF NOT EXISTS comments(id TEXT PRIMARY KEY,author TEXT,work TEXT,body TEXT,parent TEXT,deleted INTEGER DEFAULT 0,pinned INTEGER DEFAULT 0,hidden INTEGER DEFAULT 0,created INTEGER);
        CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY,a TEXT,b TEXT,initiator TEXT,status TEXT,updated INTEGER,UNIQUE(a,b));
        CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,thread TEXT,sender TEXT,body TEXT,work TEXT,created INTEGER);
        CREATE TABLE IF NOT EXISTS receipts(actor TEXT,thread TEXT,seen INTEGER,PRIMARY KEY(actor,thread));
        CREATE TABLE IF NOT EXISTS work_metrics(work TEXT PRIMARY KEY,views INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS work_view_visitors(work TEXT,visitor TEXT,seen INTEGER,PRIMARY KEY(work,visitor));
        CREATE TABLE IF NOT EXISTS reactions(actor TEXT,work TEXT,kind TEXT,PRIMARY KEY(actor,work,kind));
        CREATE TABLE IF NOT EXISTS blocks(actor TEXT,peer TEXT,PRIMARY KEY(actor,peer));
        CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,actor TEXT,sender TEXT,kind TEXT,target TEXT,body TEXT,seen INTEGER DEFAULT 0,created INTEGER);
        CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY,reporter TEXT,kind TEXT,target TEXT,reason TEXT,status TEXT DEFAULT 'pending',resolution TEXT DEFAULT '',created INTEGER,UNIQUE(reporter,kind,target));
        CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,owner TEXT,mime TEXT,name TEXT,size INTEGER,created INTEGER);
        CREATE TABLE IF NOT EXISTS drafts(actor TEXT,key TEXT,data TEXT,updated INTEGER,PRIMARY KEY(actor,key));
        ''')
        # Keep optional, unverified account email outside public profile data.
        if 'email' not in {r[1] for r in db.execute('PRAGMA table_info(users)')}:
            db.execute("ALTER TABLE users ADD COLUMN email TEXT NOT NULL DEFAULT ''")
        if 'email_verified' not in {r[1] for r in db.execute('PRAGMA table_info(users)')}:
            db.execute('ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0')
        db.executescript("""
        CREATE UNIQUE INDEX IF NOT EXISTS verified_email_unique ON users(lower(email)) WHERE email_verified=1 AND email!='';
        CREATE TABLE IF NOT EXISTS email_challenges(actor TEXT,purpose TEXT,email TEXT,code TEXT,expires INTEGER,attempts INTEGER DEFAULT 0,created INTEGER,PRIMARY KEY(actor,purpose));
        CREATE INDEX IF NOT EXISTS works_listing ON works(deleted,hidden,created DESC,id);
        CREATE INDEX IF NOT EXISTS works_author ON works(author,created DESC);
        CREATE INDEX IF NOT EXISTS messages_thread_time ON messages(thread,created,id);
        CREATE INDEX IF NOT EXISTS comments_work ON comments(work,created);
        CREATE INDEX IF NOT EXISTS notifications_actor ON notifications(actor,created DESC);
        """)
        # Add a precise comment destination without changing existing notifications.
        if 'comment_id' not in {r[1] for r in db.execute('PRAGMA table_info(notifications)')}:
            db.execute('ALTER TABLE notifications ADD COLUMN comment_id TEXT')
        if not db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            seed = json.loads((WEB/'seed.json').read_text())
            for uid, p in seed['users'].items():
                p.update(available=True, avatar='', banner='', featured=[], deliverables=[], intro=p['intro'])
                db.execute('INSERT INTO users(id,handle,data,demo) VALUES(?,?,?,1)', (uid,uid,dump(p)))
            reasons = {'wanderer':'角色设定与场景变化并置，便于观察作者如何建立视觉识别。','dusk':'从图像到声音的表达尝试，展示跨媒介创作的入口。','last-train':'以文字呈现故事起点，让剧本创作也有独立的位置。','consistency':'把检查角色连续性的判断过程说出来，而不只展示结果。'}
            for i,w in enumerate(seed['works']):
                w.update(sample=True, files=[], url='', role='原型展示素材与内容编排', process='', tools='', lookingFor='', rights=RIGHTS[0], curatedReason=reasons.get(w['id'],''))
                db.execute('INSERT INTO works(id,author,data,created,updated) VALUES(?,?,?,?,?)',(w['id'],w['author'],dump(w),now()-i*60000,now()))

class Handler(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    def log_message(self, fmt, *args): pass
    @contextmanager
    def db(self):
        d=sqlite3.connect(self.server.db_path, timeout=15); d.row_factory=sqlite3.Row
        try:
            with d:yield d
        finally:d.close()
    def output(self, value, status=200, cookie=None):
        data=dump(value).encode(); self.send_response(status)
        for k,v in {'Content-Type':'application/json; charset=utf-8','Content-Length':str(len(data)),'Cache-Control':'no-store','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin'}.items(): self.send_header(k,v)
        if status>=400:self.close_connection=True;self.send_header('Connection','close')
        if status==429:self.send_header('Retry-After','60')
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(data)
    def read(self, raw=False):
        try: n=int(self.headers.get('Content-Length','0'))
        except ValueError: raise Problem('提交格式无法识别。')
        check(0<n<=(20*1024*1024 if raw else 256000), '提交内容太大或为空。',413)
        b=self.rfile.read(n)
        if raw:return b
        check(self.headers.get('Content-Type','').split(';')[0]=='application/json','请使用正确的提交格式。',415)
        try:v=json.loads(b)
        except (ValueError,UnicodeError):raise Problem('提交内容无法读取。')
        check(isinstance(v,dict),'提交格式不正确。'); return v
    def session(self, db):
        c=SimpleCookie()
        try:c.load(self.headers.get('Cookie','')); token=c.get(self.server.cookie_name)
        except Exception:token=None
        row=db.execute('SELECT s.*,u.role,u.disabled,u.demo FROM sessions s JOIN users u ON u.id=s.actor WHERE s.token=? AND s.expires>?',(digest(token.value) if token else '',now())).fetchone()
        return row if row and (self.server.demo or not row['demo']) else None
    def login_session(self,db,uid):
        token,csrf=secrets.token_urlsafe(32),secrets.token_urlsafe(24)
        db.execute('DELETE FROM sessions WHERE expires<?',(now(),))
        db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(digest(token),uid,csrf,now()+7*86400000))
        cookie=f'{self.server.cookie_name}={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800'+('; Secure' if self.server.secure_cookie else '')
        return {'ok':True,'csrf':csrf},cookie
    def rate(self, key, limit=60, scope=None):
        k=(scope or client_ip(self.client_address[0],self.headers,self.server.trusted_proxies),key); t=time.monotonic()
        with self.server.rate_lock:
            if len(self.server.rates)>10000:
                stale=[k for k,v in self.server.rates.items() if not v or v[-1]<t-60]
                for old in stale:self.server.rates.pop(old,None)
            q=self.server.rates[k]
            while q and q[0]<t-60:q.popleft()
            check(len(self.server.rates)<=20000,'请求繁忙，请稍后再试。',429)
            check(len(q)<limit,'操作太频繁，请稍后再试。',429); q.append(t)
    def origin(self):
        host=self.headers.get('Host',''); origin=self.headers.get('Origin')
        if self.server.public_origin:
            expected=self.server.public_origin;check(host.lower()==urlsplit(expected).netloc.lower(),'访问地址与服务配置不一致。',403)
        else:
            check(urlsplit('http://'+host).hostname in ['127.0.0.1','localhost','::1'],'请使用本机预览地址。',403);expected='http://'+host
        check(not origin or origin==expected,'请从本站页面提交操作。',403)
        check(self.headers.get('Sec-Fetch-Site') not in ['cross-site'],'请从本站页面提交操作。',403)
    def participant(self,db,tid,uid):
        t=db.execute('SELECT * FROM threads WHERE id=? AND (a=? OR b=?)',(tid,uid,uid)).fetchone()
        check(t,'无法访问这段对话。',404); return t
    def block(self,db,a,b):return db.execute('SELECT 1 FROM blocks WHERE (actor=? AND peer=?) OR (actor=? AND peer=?)',(a,b,b,a)).fetchone()
    def visible_work(self,db,wid):
        w=db.execute('SELECT * FROM works WHERE id=? AND deleted=0 AND hidden=0',(wid,)).fetchone();check(w,'作品暂时无法查看。',404);return w
    def notify(self,db,actor,sender,kind,target,body,comment_id=None):
        if actor!=sender and not self.block(db,actor,sender):db.execute('INSERT INTO notifications(id,actor,sender,kind,target,body,seen,created,comment_id) VALUES(?,?,?,?,?,?,0,?,?)',(ident(),actor,sender,kind,target,body[:200],now(),comment_id))
    def validate_files(self,db,uid,files):
        check(isinstance(files,list) and len(files)<=8,'最多添加 8 张图片。'); total=0; result=[]
        for f in files:
            check(isinstance(f,dict),'图片信息有误。'); row=db.execute('SELECT * FROM uploads WHERE id=? AND owner=?',(f.get('id'),uid)).fetchone()
            check(row,'图片不属于当前账户，请重新上传。',403); total+=row['size']
            item={'id':row['id'],'name':row['name'],'size':row['size'],'url':'/api/media/'+row['id'],'caption':text(f,'caption',160)}
            for key in ['width','height']:
                value=f.get(key)
                if value is not None:
                    check(isinstance(value,int) and not isinstance(value,bool) and 0<value<=100000,'图片尺寸信息有误。');item[key]=value
            result.append(item)
        check(total<=60*1024*1024,'图片合计请控制在 60 MB 以内。'); return result
    def work_data(self,db,uid,p,old=None):
        w={k:text(p,k,n,k in ['title','body']) for k,n in [('title',60),('body',5000),('topic',80),('role',400),('process',1800),('tools',200),('lookingFor',400)]}
        w['form']=p.get('form');check(w['form'] in FORMS,'请选择内容形式。')
        if old and old.get('sample'):check(w['form']==old['form'],'示例内容请保留原形式；可另发自己的作品。')
        w['kind']=p.get('kind','作品');check(w['kind'] in ['作品','教程与经验'],'请选择作品或教程与经验。')
        w['url']=text(p,'url',1500)
        if w['url']:
            u=urlsplit(w['url']);check(u.scheme=='https' and u.hostname and not u.username and not u.password and not any(ord(c)<32 for c in w['url']),'请使用不含登录信息的完整 HTTPS 链接。')
        w['files']=self.validate_files(db,uid,p.get('files',[]))
        cover=p.get('cover');w['cover']=None
        if cover:
            check(isinstance(cover,dict),'列表展示图信息有误。')
            source=next((f for f in w['files'] if f['id']==cover.get('sourceId')),None)
            check(source,'列表展示图请从这件作品的图片中选择。')
            row=db.execute('SELECT * FROM uploads WHERE id=? AND owner=?',(cover.get('id'),uid)).fetchone()
            check(row,'列表展示图不属于当前账户。',403)
            values={k:cover.get(k,d) for k,d in [('cx',.5),('cy',.5),('zoom',1),('ratio',1.32)]}
            check(all(isinstance(v,(float,int)) and not isinstance(v,bool) and abs(v)<=4 and math.isfinite(v) for v in values.values()),'展示图构图参数有误。')
            check(0<=values['cx']<=1 and 0<=values['cy']<=1 and 1<=values['zoom']<=4 and .5<=values['ratio']<=2,'展示图构图参数超出范围。')
            w['cover']={'sourceId':source['id'],'id':row['id'],'url':'/api/media/'+row['id'],**values}
        # Samples retain their original project assets during metadata edits.
        if not (old and old.get('sample')):
            check(w['form']!='图像' or w['files'] or w['url'],'图像作品请添加图片或外部链接。')
            check(w['form'] not in ['视频','声音','互动','3D'] or w['url'],'这个形式请添加外部作品链接。')
        w['rights']=p.get('rights',RIGHTS[0]);check(w['rights'] in RIGHTS,'请选择展示与使用说明。')
        w['tags']=[t.strip() for t in re.split('[，,、]',w['topic']) if t.strip()][:6] or [w['form']]
        w['sections']=[];w['subtitle']=w['lookingFor'] or w['body'].split('\n')[0][:110]
        if old:
            for k in ['sample','image','media','secondary','sections','curatedReason']:w[k]=old.get(k)
        else:w.update(sample=False,curatedReason='')
        return w
    def state(self,db,session):
        uid=session['actor'] if session and not session['disabled'] else None
        profiles={r['id']:public_profile(r) for r in db.execute('SELECT * FROM users WHERE disabled=0')}
        if uid in profiles:
            own_data=json.loads(db.execute('SELECT data FROM users WHERE id=?',(uid,)).fetchone()[0])
            for key in ['avatarEdit','bannerEdit']:profiles[uid][key]=own_data.get(key)
        banned=[r['peer'] for r in db.execute('SELECT peer FROM blocks WHERE actor=?',(uid,))] if uid else []
        works=[];deleted=[]
        query=params(self.path)
        if query.get('author')=='self':query['author']=uid or '__guest__'
        page_rows,page=work_page(db,uid,query)
        context_ids=set()
        if query.get('view')=='notifications' and uid:
            context_ids.update(r[0] for r in db.execute("SELECT target FROM notifications WHERE actor=? AND kind='comment' ORDER BY created DESC LIMIT 200",(uid,)))
        if query.get('view')=='creators':
            for profile in profiles.values():
                candidate=db.execute('SELECT id FROM works WHERE author=? AND deleted=0 AND hidden=0 ORDER BY created DESC LIMIT 1',(profile['id'],)).fetchone()
                if candidate:context_ids.add(candidate[0])
                context_ids.update(profile.get('featured',[])[:1])
        if query.get('id'):context_ids.add(query['id'])
        if query.get('quote'):context_ids.add(query['quote'])
        if query.get('peer') and uid:
            context_ids.update(r[0] for r in db.execute('SELECT m.work FROM messages m JOIN threads t ON t.id=m.thread WHERE (t.a=? AND t.b=?) OR (t.a=? AND t.b=?) ORDER BY m.created DESC LIMIT 40',(uid,query['peer'],query['peer'],uid)) if r[0])
        # Homepage curation / representative works are context, not extra stream items.
        if not query.get('author') and not query.get('id'):
            context_ids.update(r[0] for r in db.execute("SELECT id FROM works WHERE deleted=0 AND hidden=0 AND coalesce(json_extract(data,'$.curatedReason'),'')!='' ORDER BY created DESC LIMIT 8"))
        if query.get('author') in profiles:context_ids.update(profiles[query['author']].get('featured',[])[:3])
        context_ids.difference_update(page['ids'])
        context_rows=[]
        for wid in context_ids:
            row=db.execute('SELECT * FROM works WHERE id=? AND deleted=0 AND hidden=0',(wid,)).fetchone()
            if row:context_rows.append(row)
        removed_rows=db.execute('SELECT * FROM works WHERE author=? AND (deleted=1 OR hidden=1) ORDER BY created DESC LIMIT 48',(uid,)).fetchall() if uid and not query.get('view') else []
        for r in [*page_rows,*context_rows,*removed_rows]:
            w=json.loads(r['data']);w.update(id=r['id'],author=r['author'],createdAt=r['created'],updatedAt=r['updated'],revision=r['revision'])
            if r['author'] not in profiles:continue
            if r['deleted'] or r['hidden']:
                if r['author']==uid:deleted.append({**w,'hidden':bool(r['hidden']),'deleted':bool(r['deleted'])})
                continue
            if r['author'] in banned:continue
            w['images']=[f['url'] for f in w.get('files',[])];w['image']=(w.get('cover') or {}).get('url') or (w['images'][0] if w['images'] else w.get('image'))
            works.append(w)
        visible={w['id'] for w in works}
        for profile in profiles.values():
            authored=db.execute('SELECT 1 FROM works WHERE author=? AND deleted=0 AND hidden=0 LIMIT 1',(profile['id'],)).fetchone() is not None
            profile['workCount']=db.execute('SELECT count(*) FROM works WHERE author=? AND deleted=0 AND hidden=0',(profile['id'],)).fetchone()[0]
            profile['collaborationSet']=profile.get('collaborationSet',True)
            # Legacy creators keep their presentation; new users opt in explicitly.
            profile['discoverable']=bool((self.server.demo or not profile.get('demo')) and profile.get('creatorListed',True) and authored and (profile.get('bio') or profile.get('directions')))
        comments=[{'id':r['id'],'author':r['author'],'work':r['work'],'body':r['body'] if not (r['deleted'] or r['hidden'] or r['author'] in banned) else '', 'parent':r['parent'],'deleted':bool(r['deleted'] or r['hidden'] or r['author'] in banned),'pinned':bool(r['pinned']),'time':r['created']} for r in db.execute('SELECT * FROM comments WHERE work IN ('+(','.join('?' for _ in visible) or 'NULL')+') ORDER BY pinned DESC,created',list(visible)) if r['work'] in visible and r['author'] in profiles]
        threads=[]
        if uid:
            for r in db.execute('SELECT * FROM threads WHERE a=? OR b=? ORDER BY updated DESC',(uid,uid)):
                th=dict(r);peer=th['b'] if th['a']==uid else th['a']; th['blocked']=bool(self.block(db,uid,peer));th['key']=th.pop('id')
                seen=db.execute('SELECT seen FROM receipts WHERE actor=? AND thread=?',(uid,th['key'])).fetchone();seen=seen['seen'] if seen else 0
                rows=db.execute('SELECT * FROM messages WHERE thread=? ORDER BY created DESC,id DESC LIMIT ?',(th['key'],40 if query.get('peer')==peer or not query.get('view') else 1)).fetchall()
                th['messages']=[{'id':m['id'],'sender':m['sender'],'body':m['body'],'work':m['work'],'time':m['created'],'seen':m['created']<=seen or m['sender']==uid} for m in reversed(rows)]
                th['hasOlder']=db.execute('SELECT count(*) FROM messages WHERE thread=?',(th['key'],)).fetchone()[0]>len(rows)
                th['unread']=db.execute('SELECT count(*) FROM messages WHERE thread=? AND sender!=? AND created>?',(th['key'],uid,seen)).fetchone()[0]

                threads.append(th)
        reactions=[dict(r) for r in db.execute('SELECT work,kind FROM reactions WHERE actor=?',(uid,))] if uid else []
        owner_stats={}
        if uid:
            counts={}
            for r in db.execute('SELECT r.work,r.kind,count(*) AS total FROM reactions r JOIN works w ON w.id=r.work JOIN users u ON u.id=r.actor WHERE w.author=? AND u.disabled=0 GROUP BY r.work,r.kind',(uid,)):
                counts.setdefault(r['work'],{})[r['kind']]=r['total']
            views={r['work']:r['views'] for r in db.execute('SELECT m.work,m.views FROM work_metrics m JOIN works w ON w.id=m.work WHERE w.author=?',(uid,))}
            for w in works:
                if w['author']==uid:owner_stats[w['id']]={'likes':counts.get(w['id'],{}).get('likes',0),'saved':counts.get(w['id'],{}).get('saved',0),'views':views.get(w['id'],0)}
        private_counts={kind:db.execute('SELECT count(*) FROM reactions r JOIN works w ON w.id=r.work JOIN users u ON u.id=w.author WHERE r.actor=? AND r.kind=? AND w.deleted=0 AND w.hidden=0 AND u.disabled=0 AND NOT EXISTS(SELECT 1 FROM blocks b WHERE b.actor=? AND b.peer=w.author)',(uid,kind,uid)).fetchone()[0] for kind in ['saved','likes']} if uid else {}
        return {'privateCounts':private_counts,'page':page,'mailEnabled':self.server.mail_enabled,'account':dict(db.execute('SELECT email,email_verified FROM users WHERE id=?',(uid,)).fetchone()) if uid else None,'ownerStats':owner_stats,'viewer':uid,'users':profiles,'works':works,'deletedWorks':deleted,'comments':comments,'threads':threads,'saved':[r['work'] for r in reactions if r['kind']=='saved'],'likes':[r['work'] for r in reactions if r['kind']=='likes'],'drafts':{r['key']:json.loads(r['data']) for r in db.execute('SELECT * FROM drafts WHERE actor=?',(uid,))} if uid else {},'draftTimes':{r['key']:r['updated'] for r in db.execute('SELECT key,updated FROM drafts WHERE actor=?',(uid,))} if uid else {},'blocks':banned,'notifications':[dict(r) for r in db.execute('SELECT * FROM notifications WHERE actor=? ORDER BY created DESC LIMIT 200',(uid,))] if uid else [],'csrf':session['csrf'] if uid else '', 'admin':bool(uid and session['role']=='admin'),'demoEnabled':self.server.demo,'shared':True}
    def report_evidence(self,db,r):
        result=dict(r);kind=r['kind'];target=r['target'];evidence=''
        if kind=='work':
            w=db.execute('SELECT data FROM works WHERE id=?',(target,)).fetchone()
            if w:
                w=json.loads(w['data']);evidence=w['title']+'：'+w['body'][:800]
        elif kind in ['comment','message']:
            table='comments' if kind=='comment' else 'messages'
            w=db.execute('SELECT body FROM '+table+' WHERE id=?',(target,)).fetchone();evidence=w['body'] if w else '内容不可用'
        elif kind=='user':
            u=db.execute('SELECT data FROM users WHERE id=?',(target,)).fetchone()
            if u:u=json.loads(u['data']);evidence=u['name']+'：'+u.get('bio','')
        result['evidence']=evidence;return result
    def do_GET(self):
        path=urlsplit(self.path).path
        if path=='/healthz':
            with self.db() as db:db.execute('SELECT 1')
            self.output({'ok':True,'mail':{'configured':self.server.mail_enabled,'failures':self.server.mail_failures}});return
        if path=='/api/state':
            try:
                self.rate('read',180)
                with self.db() as db:self.output(self.state(db,self.session(db)))
            except Problem as e:self.output({'error':e.message},e.status)
            except (sqlite3.Error,OSError):self.output({'error':'数据暂时无法读取，请稍后重试。'},503)
            return
        if path=='/api/messages':
            try:
                self.rate('read',180)
                with self.db() as db:
                    session=self.session(db);check(session and not session['disabled'],'请先登录再操作。',401)
                    uid=session['actor'];q=params(self.path);tid=q.get('thread','');self.participant(db,tid,uid)
                    before=q.get('before');args=[tid];condition='thread=?'
                    if before:
                        m=db.execute('SELECT created,id FROM messages WHERE id=? AND thread=?',(before,tid)).fetchone();check(m,'消息位置已变化，请刷新。',400)
                        condition+=' AND (created<? OR (created=? AND id<?))';args.extend([m['created'],m['created'],m['id']])
                    rows=db.execute('SELECT * FROM messages WHERE '+condition+' ORDER BY created DESC,id DESC LIMIT 41',args).fetchall()
                    seen=db.execute('SELECT seen FROM receipts WHERE actor=? AND thread=?',(uid,tid)).fetchone();seen=seen[0] if seen else 0
                    items=[{'id':m['id'],'sender':m['sender'],'body':m['body'],'work':m['work'],'time':m['created'],'seen':m['created']<=seen or m['sender']==uid} for m in reversed(rows[:40])]
                    quotes=[];authors={}
                    for wid in set(m['work'] for m in items if m['work']):
                        w=db.execute('SELECT w.* FROM works w JOIN users u ON u.id=w.author WHERE w.id=? AND w.deleted=0 AND w.hidden=0 AND u.disabled=0',(wid,)).fetchone()
                        if not w or db.execute('SELECT 1 FROM blocks WHERE actor=? AND peer=?',(uid,w['author'])).fetchone():continue
                        value=json.loads(w['data']);value.update(id=w['id'],author=w['author']);value['images']=[f['url'] for f in value.get('files',[])];value['image']=(value.get('cover') or {}).get('url') or (value['images'][0] if value['images'] else value.get('image'));quotes.append(value)
                        authors[w['author']]=public_profile(db.execute('SELECT * FROM users WHERE id=?',(w['author'],)).fetchone())
                    self.output({'messages':items,'hasOlder':len(rows)>40,'works':quotes,'users':authors})
            except Problem as e:self.output({'error':e.message},e.status)
            return
        if path=='/api/admin':
            with self.db() as db:
                s=self.session(db)
                if not s or s['disabled'] or s['role']!='admin':self.output({'error':'需要管理员权限。'},403);return
                self.output({'reports':[self.report_evidence(db,r) for r in db.execute('SELECT * FROM reports ORDER BY created DESC')], 'works':[dict(id=r['id'],author=r['author'],title=json.loads(r['data'])['title'],hidden=bool(r['hidden']),deleted=bool(r['deleted']),curatedReason=json.loads(r['data']).get('curatedReason','')) for r in db.execute('SELECT * FROM works')], 'users':[{'id':r['id'],'name':json.loads(r['data'])['name'],'disabled':bool(r['disabled']),'demo':bool(r['demo'])} for r in db.execute('SELECT * FROM users')]})
            return
        if path.startswith('/api/media/'):
            fid=path.removeprefix('/api/media/')
            if not re.fullmatch('[a-f0-9]{24}',fid):self.send_error(404);return
            with self.db() as db:
                r=db.execute('SELECT * FROM uploads WHERE id=?',(fid,)).fetchone();s=self.session(db);allowed=bool(r and s and s['actor']==r['owner'] and not s['disabled'])
                if r and not allowed:
                    allowed=any(fid in [f['id'] for f in json.loads(w['data']).get('files',[])]+[(json.loads(w['data']).get('cover') or {}).get('id')] for w in db.execute('SELECT data FROM works WHERE deleted=0 AND hidden=0 AND author IN (SELECT id FROM users WHERE disabled=0)')) or any(fid in [json.loads(u['data']).get(k,'').rsplit('/',1)[-1] for k in ['avatar','banner']] for u in db.execute('SELECT data FROM users WHERE disabled=0'))
                if not allowed:self.send_error(404);return
                file=self.server.media_path/fid
                if not file.is_file():self.send_error(404);return
                self.file(file,r['mime']);return
        if path.startswith('/api/'):
            self.output({'error':'没有找到这个接口。'},404);return
        file=(WEB/(path.lstrip('/') or 'index.html')).resolve()
        if not file.is_relative_to(WEB) or not file.is_file() or file.name=='seed.json':self.send_error(404);return
        self.file(file,mimetypes.guess_type(file)[0] or 'application/octet-stream')
    def file(self,file,mime):
        data=file.read_bytes();self.send_response(200)
        for k,v in {'Content-Type':mime,'Content-Length':str(len(data)),'Cache-Control':'no-cache','X-Content-Type-Options':'nosniff','Referrer-Policy':'same-origin','Content-Security-Policy':"default-src 'self'; img-src 'self' blob: data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'"}.items():self.send_header(k,v)
        self.end_headers();self.wfile.write(data)
    def do_POST(self):self.api()
    def do_PATCH(self):self.api()
    def do_DELETE(self):self.api()
    def api(self):
        path=urlsplit(self.path).path;cookie=None
        try:
            self.origin();self.rate('auth' if path.startswith('/api/auth') else 'write',12 if path.startswith('/api/auth') else 100)
            with self.db() as db:
                db.execute('BEGIN IMMEDIATE');s=self.session(db);uid=s['actor'] if s else None
                if path=='/api/view':
                    if s:
                        check(not s['disabled'],'账户暂时不可用。',403)
                        check(hmac.compare_digest(self.headers.get('X-CSRF-Token',''),s['csrf']),'页面身份已变化，请刷新后重试。',403)
                    result,cookie=self.record_view(db,self.read(),uid)
                elif path.startswith('/api/auth/'):
                    p=self.read();result,cookie=self.auth(db,path,p,s)
                else:
                    check(s and not s['disabled'],'请先登录再操作。',401)
                    check(hmac.compare_digest(self.headers.get('X-CSRF-Token',''),s['csrf']),'页面身份已变化，请刷新后重试。',403)
                    if path=='/api/upload':
                        result=self.upload(db,uid)
                    elif path.startswith('/api/account/'):
                        result,cookie=self.email_action(db,path,self.read(),s)
                    else:result=self.change(db,uid,path,self.read(),s)
                db.commit()
            self.output(result,cookie=cookie)
        except Problem as e:self.output({'error':e.message},e.status)
        except (ValueError,TypeError,KeyError,sqlite3.IntegrityError):self.output({'error':'提交内容有误或状态已变化，请刷新后重试。'},400)
        except Exception:
            self.output({'error':'这次没有保存成功，请稍后重试。'},500)
    def record_view(self,db,p,uid):
        wid=text(p,'id',80,True);w=self.visible_work(db,wid)
        check(db.execute('SELECT 1 FROM users WHERE id=? AND disabled=0',(w['author'],)).fetchone(),'作品暂时无法查看。',404)
        check(not uid or not self.block(db,uid,w['author']),'作品暂时无法查看。',404)
        if uid==w['author']:return {'ok':True,'counted':False},None
        cookie=None
        if uid:visitor=digest('account:'+uid)
        else:
            jar=SimpleCookie()
            try:jar.load(self.headers.get('Cookie',''));v=jar.get(self.server.cookie_name+'_visitor')
            except Exception:v=None
            token=v.value if v and re.fullmatch('[A-Za-z0-9_-]{32,64}',v.value) else None
            if not token:
                token=secrets.token_urlsafe(24)
                cookie=f'{self.server.cookie_name}_visitor={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=2592000'+('; Secure' if self.server.secure_cookie else '')
            visitor=digest('visitor:'+token)
        stamp=now();window=30*60000
        db.execute('DELETE FROM work_view_visitors WHERE seen<=?',(stamp-window,))
        previous=db.execute('SELECT seen FROM work_view_visitors WHERE work=? AND visitor=?',(wid,visitor)).fetchone()
        if previous:return {'ok':True,'counted':False},cookie
        db.execute('INSERT INTO work_view_visitors VALUES(?,?,?)',(wid,visitor,stamp))
        db.execute('INSERT INTO work_metrics VALUES(?,1) ON CONFLICT(work) DO UPDATE SET views=views+1',(wid,))
        return {'ok':True,'counted':True},cookie

    def auth(self,db,path,p,s):
        if path=='/api/auth/logout':
            check(s and hmac.compare_digest(self.headers.get('X-CSRF-Token',''),s['csrf']),'请刷新页面后退出。',403)
            db.execute('DELETE FROM sessions WHERE token=?',(s['token'],));return {'ok':True},self.server.cookie_name+'=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0'
        if path=='/api/auth/demo':
            check(self.server.demo,'示例身份仅用于本机开发预览。',403);uid=p.get('id');check(db.execute('SELECT 1 FROM users WHERE id=? AND demo=1 AND disabled=0',(uid,)).fetchone(),'示例身份不可用。');return self.login_session(db,uid)
        if path in ['/api/auth/email-request','/api/auth/email-reset']:return self.email_action(db,path,p,s)
        handle=text(p,'handle',32).lower();password=p.get('password','');check(isinstance(password,str) and len(password)<=128,'密码格式有误。')
        check(re.fullmatch('[a-z][a-z0-9_]{2,31}',handle),'账号使用 3–32 位小写字母、数字或下划线，以字母开头。')
        if path=='/api/auth/register':
            check(10<=len(password)<=128,'密码至少 10 位。');check(not db.execute('SELECT 1 FROM users WHERE handle=?',(handle,)).fetchone(),'这个账号名已使用。',409)
            name=text(p,'name',24,True);email=text(p,'email',254)
            if email:
                local,_,domain=email.partition('@')
                check(len(local)<=64 and not local.startswith('.') and not local.endswith('.') and '..' not in local and re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",email),'请填写有效的邮箱地址，或留空。')
                email=local+'@'+domain.lower()
            uid,salt,code=ident(),secrets.token_hex(16),secrets.token_urlsafe(24)
            profile={'name':name,'bio':'','intro':'','location':'','directions':[],'collaboration':[],'collaborationSet':False,'creatorListed':False,'available':False,'avatar':'','banner':'','featured':[],'deliverables':[]}
            db.execute('INSERT INTO users(id,handle,password,salt,recovery,data,email) VALUES(?,?,?,?,?,?,?)',(uid,handle,password_hash(password,salt),salt,digest(code),dump(profile),email))
            result,cookie=self.login_session(db,uid);result['recoveryCode']=code;return result,cookie
        row=db.execute('SELECT * FROM users WHERE handle=? AND demo=0 AND disabled=0',(handle,)).fetchone()
        if path=='/api/auth/login':
            # Constant-cost verification also when account does not exist.
            value=password_hash(password,row['salt'] if row else '00'*16)
            check(row and hmac.compare_digest(value,row['password']),'账号或密码不正确。',401);return self.login_session(db,row['id'])
        if path=='/api/auth/recover':
            check(10<=len(password)<=128,'新密码至少 10 位。');code=text(p,'code',100,True)
            check(row and hmac.compare_digest(digest(code),row['recovery']),'账号或恢复码不正确。',401)
            salt,newcode=secrets.token_hex(16),secrets.token_urlsafe(24)
            db.execute('UPDATE users SET password=?,salt=?,recovery=? WHERE id=?',(password_hash(password,salt),salt,digest(newcode),row['id']));db.execute('DELETE FROM sessions WHERE actor=?',(row['id'],));db.execute('DELETE FROM email_challenges WHERE actor=?',(row['id'],))
            result,cookie=self.login_session(db,row['id']);result['recoveryCode']=newcode;return result,cookie
        raise Problem('不支持这个操作。',404)
    def email_address(self,value):
        check(isinstance(value,str) and len(value)<=254,'请填写有效的邮箱地址。')
        value=value.strip();local,_,domain=value.partition('@')
        check(len(local)<=64 and not local.startswith('.') and not local.endswith('.') and '..' not in local and re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?",value),'请填写有效的邮箱地址。')
        return local+'@'+domain.lower()

    def email_challenge(self,db,row,purpose):
        check(self.server.mail_slots.acquire(blocking=False),'邮件发送繁忙，请稍后重试。',503)
        code=f'{secrets.randbelow(100000000):08d}';stamp=now()
        try:
            db.execute('DELETE FROM email_challenges WHERE expires<?',(stamp,))
            db.execute('INSERT OR REPLACE INTO email_challenges(actor,purpose,email,code,expires,attempts,created) VALUES(?,?,?,?,?,0,?)',(row['id'],purpose,row['email'],digest(row['id']+purpose+code),stamp+15*60000,stamp))
            # Commit before the background sender can deliver an active code.
            db.commit()
        except Exception:
            self.server.mail_slots.release();raise
        recipient=row['email'];subject='幻场 · '+('验证邮箱' if purpose=='verify' else '找回账户')
        body=f'你的幻场{subject.split(" · ")[1]}验证码为：{code}\n15分钟内有效，只能使用一次。\n如果不是你发起的操作，请忽略这封邮件。请勿把验证码交给他人。'
        def deliver():
            try:self.server.mail_sender(recipient,subject,body)
            except Exception:self.server.mail_failures+=1  # No addresses/codes/secrets in logs.
            finally:self.server.mail_slots.release()
        dispatch=getattr(self.server,'mail_dispatch',None)
        if dispatch:dispatch(deliver)
        else:threading.Thread(target=deliver,daemon=True).start()

    def consume_email_code(self,db,row,purpose,code):
        challenge=db.execute('SELECT * FROM email_challenges WHERE actor=? AND purpose=?',(row['id'],purpose)).fetchone()
        check(challenge and challenge['expires']>now() and challenge['attempts']<5 and challenge['email']==row['email'],'验证码无效或已过期，请重新获取。',400)
        db.execute('UPDATE email_challenges SET attempts=attempts+1 WHERE actor=? AND purpose=?',(row['id'],purpose))
        valid=isinstance(code,str) and hmac.compare_digest(digest(row['id']+purpose+code.strip()),challenge['code'])
        if not valid:db.commit();raise Problem('验证码无效或已过期，请重新获取。',400)
        db.execute('DELETE FROM email_challenges WHERE actor=? AND purpose=?',(row['id'],purpose))

    def email_action(self,db,path,p,s):
        if path=='/api/auth/email-request':
            check(self.server.mail_enabled,'邮件服务尚未配置，可使用恢复码找回。',503)
            handle=text(p,'handle',32).lower();email=self.email_address(p.get('email',''))
            self.rate('email-reset:'+digest(handle),3)
            self.rate('email-target:'+digest(email.lower()),3,scope='mail')
            row=db.execute('SELECT * FROM users WHERE handle=? AND lower(email)=lower(?) AND email_verified=1 AND disabled=0 AND demo=0',(handle,email)).fetchone()
            if row:self.email_challenge(db,row,'reset')
            return {'ok':True,'message':'如果账号与已验证邮箱匹配，我们将发送验证码。请检查邮箱和垃圾邮件文件夹。'},None
        if path=='/api/auth/email-reset':
            handle=text(p,'handle',32).lower();email=self.email_address(p.get('email',''));password=p.get('password','')
            check(isinstance(password,str) and 10<=len(password)<=128,'新密码至少10位。')
            row=db.execute('SELECT * FROM users WHERE handle=? AND lower(email)=lower(?) AND email_verified=1 AND disabled=0 AND demo=0',(handle,email)).fetchone()
            check(row,'验证码无效或已过期，请重新获取。',400)
            self.consume_email_code(db,row,'reset',p.get('code',''))
            salt,newcode=secrets.token_hex(16),secrets.token_urlsafe(24)
            db.execute('UPDATE users SET password=?,salt=?,recovery=? WHERE id=?',(password_hash(password,salt),salt,digest(newcode),row['id']))
            db.execute('DELETE FROM sessions WHERE actor=?',(row['id'],));db.execute('DELETE FROM email_challenges WHERE actor=?',(row['id'],))
            result,cookie=self.login_session(db,row['id']);result['recoveryCode']=newcode;return result,cookie
        check(s and not s['disabled'],'请先登录再操作。',401)
        row=db.execute('SELECT * FROM users WHERE id=?',(s['actor'],)).fetchone();check(not row['demo'],'请用自己的注册账户设置邮箱。')
        if path=='/api/account/email':
            password=p.get('password','');check(isinstance(password,str) and len(password)<=128,'密码格式有误。')
            check(hmac.compare_digest(password_hash(password,row['salt']),row['password']),'当前密码不正确。',400)
            raw_email=p.get('email','');check(isinstance(raw_email,str),'请填写有效的邮箱地址。');email=self.email_address(raw_email) if raw_email.strip() else ''
            check(not email or not db.execute('SELECT 1 FROM users WHERE lower(email)=lower(?) AND email_verified=1 AND id!=?',(email,row['id'])).fetchone(),'这个邮箱已绑定其他账户。',409)
            if email!=row['email']:
                db.execute('UPDATE users SET email=?,email_verified=0 WHERE id=?',(email,row['id']));db.execute('DELETE FROM email_challenges WHERE actor=?',(row['id'],))
            return {'ok':True},None
        if path=='/api/account/email-request':
            check(self.server.mail_enabled,'邮件服务尚未配置，暂时无法发送。',503)
            check(row['email'] and not row['email_verified'],'请先填写未验证的邮箱。')
            self.rate('email-verify:'+row['id'],3,scope='mail');self.email_challenge(db,row,'verify')
            return {'ok':True,'message':'验证码已请求，请检查邮箱。15分钟内有效。'},None
        if path=='/api/account/email-verify':
            check(not db.execute('SELECT 1 FROM users WHERE lower(email)=lower(?) AND email_verified=1 AND id!=?',(row['email'],row['id'])).fetchone(),'这个邮箱已绑定其他账户。',409)
            self.consume_email_code(db,row,'verify',p.get('code',''))
            db.execute('UPDATE users SET email_verified=1 WHERE id=?',(row['id'],));return {'ok':True},None
        if path=='/api/account/recovery-code':
            password=p.get('password','');check(isinstance(password,str) and len(password)<=128 and hmac.compare_digest(password_hash(password,row['salt']),row['password']),'当前密码不正确。',400)
            code=secrets.token_urlsafe(24);db.execute('UPDATE users SET recovery=? WHERE id=?',(digest(code),row['id']))
            return {'ok':True,'recoveryCode':code},None
        raise Problem('没有找到这个操作。',404)

    def upload(self,db,uid):
        body=self.read(True);mime=self.headers.get('Content-Type','')
        valid=(mime=='image/png' and body.startswith(b'\x89PNG\r\n\x1a\n')) or (mime=='image/jpeg' and body.startswith(b'\xff\xd8\xff')) or (mime=='image/webp' and body.startswith(b'RIFF') and body[8:12]==b'WEBP')
        check(valid,'请选择有效的 JPG、PNG 或 WebP 图片。')
        used=db.execute('SELECT COALESCE(sum(size),0) FROM uploads WHERE owner=?',(uid,)).fetchone()[0];check(used+len(body)<=500*1024*1024,'当前账户图片空间已达 500 MB，请联系维护者。',413)
        fid=ident();name=Path(unquote(self.headers.get('X-File-Name','图片'))).name[:160];self.server.media_path.mkdir(parents=True,exist_ok=True);(self.server.media_path/fid).write_bytes(body)
        db.execute('INSERT INTO uploads VALUES(?,?,?,?,?,?)',(fid,uid,mime,name,len(body),now()));return {'id':fid,'name':name,'size':len(body),'url':'/api/media/'+fid}
    def change(self,db,uid,path,p,s):
        if path=='/api/profile':
            row=db.execute('SELECT * FROM users WHERE id=?',(uid,)).fetchone();u=json.loads(row['data']);check(p.get('revision')==u.get('revision',1),'主页已在另一处更新。请保留草稿并读取最新版本。',409);u['revision']=u.get('revision',1)+1
            for key,limit in [('name',24),('bio',120),('intro',1600),('location',40)]:u[key]=text(p,key,limit,key=='name')
            for key,limit in [('directions',6),('deliverables',6),('collaboration',3),('featured',3)]:
                v=p.get(key,[]);check(isinstance(v,list) and len(v)<=limit and all(isinstance(t,str) and len(t)<=80 for t in v),'个人资料选项有误。');u[key]=v
            check(all(v in MODES for v in u['collaboration']),'合作方式有误。');u['available']=bool(p.get('available',u.get('available',False)))
            u['collaborationSet']=bool(p.get('collaborationSet',u.get('collaborationSet',True)))
            u['creatorListed']=bool(p.get('creatorListed',u.get('creatorListed',True)))
            if u['creatorListed']:check(u['bio'] or u['directions'],'加入创作者发现前，请填写一句话介绍或创作方向。')
            for wid in u['featured']:check(self.visible_work(db,wid)['author']==uid,'代表作请选自己的作品。')
            for key in ['avatar','banner']:
                fid=p.get(key,'').removeprefix('/api/media/');u[key]=''
                if fid:check(db.execute('SELECT 1 FROM uploads WHERE id=? AND owner=?',(fid,uid)).fetchone(),'请上传自己的展示图片。',403);u[key]='/api/media/'+fid
                edit=p.get(key+'Edit');u[key+'Edit']=None
                if fid and edit is not None:
                    check(isinstance(edit,dict),'图片编辑信息有误。')
                    source=text(edit,'source',100);original=source.removeprefix('/api/media/')
                    check(source=='/api/media/'+original and db.execute('SELECT 1 FROM uploads WHERE id=? AND owner=?',(original,uid)).fetchone(),'请使用自己的原图。',403)
                    check(all(type(edit.get(k)) in [int,float] and 0<=edit[k]<=1 for k in ['cx','cy']) and type(edit.get('zoom')) in [int,float] and 1<=edit['zoom']<=4,'图片裁剪参数有误。')
                    u[key+'Edit']={k:edit[k] for k in ['source','cx','cy','zoom']}
            db.execute('UPDATE users SET data=? WHERE id=?',(dump(u),uid));db.execute('DELETE FROM drafts WHERE actor=? AND key=?',(uid,'profile'));return {'ok':True}
        if path=='/api/works':
            wid=p.get('id') or ident();check(isinstance(wid,str) and len(wid)<=80,'内容标识无效。')
            old=db.execute('SELECT * FROM works WHERE id=?',(wid,)).fetchone()
            if old:
                check(old['author']==uid,'只能编辑自己的内容。',403);check(not old['deleted'] and not old['hidden'],'内容已下架或删除，暂时不能编辑。',409);check(p.get('revision')==old['revision'],'内容已在另一处更新。请保留草稿并刷新后再编辑。',409)
            w=self.work_data(db,uid,p,json.loads(old['data']) if old else None)
            if old:db.execute('UPDATE works SET data=?,updated=?,revision=revision+1 WHERE id=?',(dump(w),now(),wid))
            else:db.execute('INSERT INTO works(id,author,data,created,updated) VALUES(?,?,?,?,?)',(wid,uid,dump(w),now(),now()))
            db.execute('DELETE FROM drafts WHERE actor=? AND key=?',(uid,p.get('draftKey','publish')));return {'ok':True,'id':wid}
        if path=='/api/work-status':
            w=db.execute('SELECT * FROM works WHERE id=?',(p.get('id'),)).fetchone();check(w and w['author']==uid,'只能管理自己的内容。',403)
            check(p.get('action') in ['delete','restore'],'不支持这个操作。');check(not w['hidden'],'内容由管理员下架，请通过举报处理渠道联系。',403)
            db.execute('UPDATE works SET deleted=?,updated=?,revision=revision+1 WHERE id=?',(int(p['action']=='delete'),now(),w['id']));return {'ok':True}
        if path=='/api/draft':
            key=text(p,'key',100,True);data=p.get('data');check(data is None or isinstance(data,(dict,str)),'草稿格式有误。')
            if data is None:db.execute('DELETE FROM drafts WHERE actor=? AND key=?',(uid,key))
            else:check(len(dump(data))<=32000,'草稿过大。');db.execute('INSERT INTO drafts VALUES(?,?,?,?) ON CONFLICT(actor,key) DO UPDATE SET data=excluded.data,updated=excluded.updated',(uid,key,dump(data),now()))
            return {'ok':True}
        if path=='/api/reaction':
            self.visible_work(db,p.get('id'));check(p.get('kind') in ['saved','likes'],'不支持这个操作。')
            if p.get('enabled'):db.execute('INSERT OR IGNORE INTO reactions VALUES(?,?,?)',(uid,p['id'],p['kind']))
            else:db.execute('DELETE FROM reactions WHERE actor=? AND work=? AND kind=?',(uid,p['id'],p['kind']))
            return {'ok':True}
        if path=='/api/comment':
            w=self.visible_work(db,p.get('work'));check(not self.block(db,uid,w['author']),'对方已屏蔽交流或你已屏蔽对方。',403);body=text(p,'body',1000,True);parent=p.get('parent')
            recipient=w['author']
            if parent:
                c=db.execute('SELECT * FROM comments WHERE id=? AND work=?',(parent,w['id'])).fetchone();check(c and not c['hidden'],'回复内容已不可用。');check(not self.block(db,uid,c['author']),'暂时无法回复这位用户。',403);recipient=c['author'];parent=c['parent'] or c['id']
            cid=p.get('id') or ident();check(isinstance(cid,str) and len(cid)<=80,'讨论标识有误。');existing=db.execute('SELECT * FROM comments WHERE id=?',(cid,)).fetchone()
            if existing:check(existing['author']==uid and existing['work']==w['id'],'讨论标识已使用。',409);return {'ok':True,'id':cid}
            db.execute('INSERT INTO comments(id,author,work,body,parent,created) VALUES(?,?,?,?,?,?)',(cid,uid,w['id'],body,parent,now()));self.notify(db,recipient,uid,'comment',w['id'],('回复了你的讨论' if parent else '在你的作品下参与讨论')+'：'+body,cid)
            db.execute('DELETE FROM drafts WHERE actor=? AND key=?',(uid,'reply:'+w['id']+':'+str(p.get('parent')) if p.get('parent') else 'comment:'+w['id']));return {'ok':True,'id':cid}
        if path=='/api/comment-action':
            c=db.execute('SELECT * FROM comments WHERE id=?',(p.get('id'),)).fetchone();check(c,'讨论不可用。',404)
            if p.get('action')=='delete':check(c['author']==uid,'只能删除自己的讨论。',403);db.execute('UPDATE comments SET deleted=1,body=? WHERE id=?',('',c['id']))
            elif p.get('action')=='pin':check(self.visible_work(db,c['work'])['author']==uid,'只有作品作者能置顶讨论。',403);check(not c['deleted'] and not c['parent'],'只能置顶未删除的主讨论。');db.execute('UPDATE comments SET pinned=0 WHERE work=?',(c['work'],));db.execute('UPDATE comments SET pinned=? WHERE id=?',(int(not c['pinned']),c['id']))
            else:raise Problem('不支持这个操作。')
            return {'ok':True}
        if path=='/api/message':
            peer=p.get('peer');other=db.execute('SELECT demo FROM users WHERE id=? AND disabled=0',(peer,)).fetchone();check(peer!=uid and other,'请选择另一位用户。');check(self.server.demo or not other['demo'],'这是展示样例，不接收真实私信。');check(not self.block(db,uid,peer),'双方之间暂时不能发送消息。',403)
            body=text(p,'body',2000,True);work=p.get('work') or None
            if work:self.visible_work(db,work)
            a,b=sorted([uid,peer]);th=db.execute('SELECT * FROM threads WHERE a=? AND b=?',(a,b)).fetchone();mid=p.get('id') or ident();check(isinstance(mid,str) and len(mid)<=80,'消息标识无效。')
            prev=db.execute('SELECT * FROM messages WHERE id=?',(mid,)).fetchone()
            if prev:check(prev['sender']==uid and th and prev['thread']==th['id'],'消息标识已使用。',409);return {'ok':True,'thread':th['id']}
            replying=bool(th and th['status']=='pending' and th['initiator']!=uid and p.get('accept') is True)
            if th:check(th['status'] not in ['pending','closed'] or replying,'请等待对方回复，或查看已关闭的联系。',409)
            if not th:
                tid=ident();db.execute('INSERT INTO threads VALUES(?,?,?,?,?,?)',(tid,a,b,uid,'pending',now()))
            else:
                tid=th['id'];db.execute('UPDATE threads SET status=?,initiator=?,updated=? WHERE id=?',('pending' if th['status']=='withdrawn' else 'accepted',uid if th['status']=='withdrawn' else th['initiator'],now(),tid))
            last=db.execute('SELECT COALESCE(max(created),0) FROM messages WHERE thread=?',(tid,)).fetchone()[0];stamp=max(now(),last+1)
            db.execute('INSERT INTO messages VALUES(?,?,?,?,?,?)',(mid,tid,uid,body,work,stamp));db.execute('DELETE FROM drafts WHERE actor=? AND key=?',(uid,'message:'+peer));self.notify(db,peer,uid,'message',uid,'发来一条消息：'+body);return {'ok':True,'thread':tid}
        if path=='/api/thread-action':
            t=self.participant(db,p.get('id'),uid);action=p.get('action');check(not self.block(db,t['a'],t['b']),'双方之间暂时不能继续交流。',403)
            if action=='read':db.execute('INSERT INTO receipts VALUES(?,?,?) ON CONFLICT(actor,thread) DO UPDATE SET seen=excluded.seen',(uid,t['id'],db.execute('SELECT COALESCE(max(created),0) FROM messages WHERE thread=?',(t['id'],)).fetchone()[0]));db.execute('UPDATE notifications SET seen=1 WHERE actor=? AND kind=? AND target=?',(uid,'message',t['b'] if uid==t['a'] else t['a']))
            else:
                if action=='reopen':
                    check(t['status']=='closed','联系状态已经变化。',409);check(t['initiator']!=uid,'只有接收方可以重新开启此次联系。',403);status='accepted'
                else:
                    check(t['status']=='pending','联系状态已经变化。',409)
                    if action=='withdraw':check(t['initiator']==uid,'只有发起人能取消此次联系。',403);status='withdrawn'
                    else:check(t['initiator']!=uid,'请等待对方回应。',403);check(action in ['accept','close'],'不支持这个操作。');status='accepted' if action=='accept' else 'closed'
                db.execute('UPDATE threads SET status=?,updated=? WHERE id=?',(status,now(),t['id']));peer=t['b'] if t['a']==uid else t['a'];self.notify(db,peer,uid,'message',uid,{'accepted':'已重新开启对话' if action=='reopen' else '已开始对话','withdrawn':'已取消此次联系，之前的消息记录保留','closed':'未接受此次联系'}[status])
            return {'ok':True}
        if path=='/api/notifications':
            if p.get('id'):db.execute('UPDATE notifications SET seen=1 WHERE id=? AND actor=?',(p['id'],uid))
            else:db.execute('UPDATE notifications SET seen=1 WHERE actor=?',(uid,))
            return {'ok':True}
        if path=='/api/block':
            peer=p.get('peer');check(peer!=uid and db.execute('SELECT 1 FROM users WHERE id=?',(peer,)).fetchone(),'屏蔽对象不可用。')
            if p.get('enabled'):db.execute('INSERT OR IGNORE INTO blocks VALUES(?,?)',(uid,peer))
            else:db.execute('DELETE FROM blocks WHERE actor=? AND peer=?',(uid,peer))
            return {'ok':True}
        if path=='/api/report':
            kind,target=p.get('kind'),p.get('target');check(kind in ['work','comment','user','message'],'举报类型无效。');reason=text(p,'reason',500,True)
            if kind=='work':self.visible_work(db,target)
            elif kind=='user':check(db.execute('SELECT 1 FROM users WHERE id=?',(target,)).fetchone(),'用户不存在。')
            elif kind=='comment':check(db.execute('SELECT 1 FROM comments WHERE id=?',(target,)).fetchone(),'讨论不存在。')
            else:
                m=db.execute('SELECT * FROM messages WHERE id=?',(target,)).fetchone();check(m,'消息不存在。');self.participant(db,m['thread'],uid)
            db.execute('INSERT INTO reports(id,reporter,kind,target,reason,created) VALUES(?,?,?,?,?,?) ON CONFLICT(reporter,kind,target) DO NOTHING',(ident(),uid,kind,target,reason,now()));return {'ok':True}
        if path=='/api/admin-action':
            check(s['role']=='admin','需要管理员权限。',403);action=p.get('action')
            if action=='curate':
                w=db.execute('SELECT * FROM works WHERE id=?',(p.get('id'),)).fetchone();check(w,'作品不存在。');data=json.loads(w['data']);data['curatedReason']=text(p,'reason',250);db.execute('UPDATE works SET data=?,revision=revision+1 WHERE id=?',(dump(data),w['id']))
            elif action=='resolve':
                r=db.execute('SELECT * FROM reports WHERE id=?',(p.get('id'),)).fetchone();check(r and r['status']=='pending','举报已处理或不存在。',409);decision=p.get('decision');check(decision in ['dismiss','hide'],'请选择处理方式。');note=text(p,'note',500,True)
                if decision=='hide':
                    if r['kind']=='work':db.execute('UPDATE works SET hidden=1 WHERE id=?',(r['target'],))
                    elif r['kind']=='comment':db.execute('UPDATE comments SET hidden=1 WHERE id=?',(r['target'],))
                    elif r['kind']=='user':check(r['target']!=uid,'不能停用当前管理员。');db.execute('UPDATE users SET disabled=1 WHERE id=?',(r['target'],));db.execute('DELETE FROM sessions WHERE actor=?',(r['target'],))
                    else:raise Problem('私信举报请先调查；本版不删除对话记录。')
                db.execute('UPDATE reports SET status=?,resolution=? WHERE id=?',('resolved',note,r['id']));self.notify(db,r['reporter'],uid,'report',r['id'],'你的举报已处理：'+note)
            elif action=='restore-work':db.execute('UPDATE works SET hidden=0 WHERE id=?',(p.get('id'),))
            elif action=='restore-user':db.execute('UPDATE users SET disabled=0 WHERE id=?',(p.get('id'),))
            else:raise Problem('不支持这个操作。')
            return {'ok':True}
        raise Problem('没有找到这个操作。',404)

def make_server(port=8790,db_path=None,media_path=None,demo=False,host='127.0.0.1',secure_cookie=False,public_origin=None,trusted_proxies='',mail_sender=None):
    if host not in ['127.0.0.1','localhost','::1']:
        origin=urlsplit(public_origin or '')
        if demo or not secure_cookie or origin.scheme!='https' or not origin.hostname or origin.username or origin.password or origin.path not in ['','/'] or origin.query or origin.fragment:
            raise ValueError('对外监听必须使用正式HTTPS来源、安全登录Cookie，并关闭示例身份。')
    trusted=proxy_networks(trusted_proxies);sender=mail_sender or SMTPDelivery()
    db_path=Path(db_path or ROOT/'work/huanchang/huanchang.sqlite3');initialize(db_path)
    server=ThreadingHTTPServer((host,port),Handler);server.db_path=db_path;server.cookie_name='huanchang_session_'+str(server.server_port);server.media_path=Path(media_path or ROOT/'work/huanchang/media');server.demo=demo;server.secure_cookie=secure_cookie;server.public_origin=public_origin;server.rates=defaultdict(deque);server.rate_lock=threading.Lock();server.trusted_proxies=trusted;server.mail_sender=sender;server.mail_enabled=bool(mail_sender or server.mail_sender.enabled);server.mail_failures=0;server.mail_slots=threading.BoundedSemaphore(8);return server

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=8790);p.add_argument('--host',default='127.0.0.1');p.add_argument('--db',type=Path);p.add_argument('--media',type=Path);p.add_argument('--demo',action='store_true');p.add_argument('--secure-cookie',action='store_true');p.add_argument('--public-origin',default=os.environ.get('HUANCHANG_PUBLIC_ORIGIN')); p.add_argument('--trusted-proxies',default=os.environ.get('HUANCHANG_TRUSTED_PROXIES','')); p.add_argument('--make-admin',help='Promote an existing registered handle, then exit.')
    a=p.parse_args();db_path=a.db or ROOT/'work/huanchang/huanchang.sqlite3'
    if a.make_admin:
        initialize(db_path)
        with closing(sqlite3.connect(db_path)) as db, db:
            row=db.execute('SELECT id FROM users WHERE handle=? AND demo=0',(a.make_admin,)).fetchone()
            if not row:raise SystemExit('Registered account not found.')
            db.execute("UPDATE users SET role='admin' WHERE id=?",(row[0],))
        print('Administrator role set.');return
    if a.demo and a.host not in ['127.0.0.1','localhost','::1']:raise SystemExit('--demo may only bind loopback.')
    if a.public_origin:
        u=urlsplit(a.public_origin);check(u.scheme=='https' and u.netloc and not u.username and not u.password and u.path in ['','/'] and not u.query and not u.fragment,'正式站点地址请填写 HTTPS 域名根地址。');a.public_origin=a.public_origin.rstrip('/')
    s=make_server(a.port,db_path,a.media,a.demo,a.host,a.secure_cookie,a.public_origin,a.trusted_proxies);print(f'幻场本地预览 http://{a.host}:{s.server_port}/',flush=True)
    try:s.serve_forever()
    except KeyboardInterrupt:pass
    finally:s.server_close()
if __name__=='__main__':main()
