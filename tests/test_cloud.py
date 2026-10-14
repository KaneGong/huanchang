"""Cloud checks require an explicit disposable loopback PostgreSQL database.
The default dependency-free local test suite skips these checks.
"""
import os
import json
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch
try:
    import psycopg
    from cloud.database import connect
    from cloud.runtime import create_app
    AVAILABLE = True
except ImportError:
    AVAILABLE = False
URL = os.getenv('HUANCHANG_TEST_DATABASE_URL','')

class StorageDouble:
    origin='https://test.supabase.co'
    def __init__(self):self.objects={}
    def signed(self,fid,upload=False):return self.origin+'/storage/v1/object/'+('upload/' if upload else '')+fid+'?token=test-only'
    def inspect(self,fid):return self.objects[fid][:32],len(self.objects[fid])

class Client:
    def __init__(self,app):self.client=app.test_client();self.csrf=''
    def get(self,path):return self.client.get('/api/'+path)
    def post(self,path,data):
        r=self.client.post('/api/'+path,json=data,headers={'X-CSRF-Token':self.csrf})
        if r.json and r.json.get('csrf'):self.csrf=r.json['csrf']
        return r
    def ok(self,path,data):
        r=self.post(path,data);assert r.status_code==200,(path,r.status_code,r.json);return r.json

@unittest.skipUnless(AVAILABLE and URL,'Optional cloud dependencies and disposable database required')
class CloudTests(unittest.TestCase):
    def setUp(self):
        if urlsplit(URL).hostname not in ['127.0.0.1','localhost']:raise RuntimeError('Cloud regression only accepts loopback test DB')
        with psycopg.connect(URL,prepare_threshold=None) as db:
            db.execute(Path('cloud/schema.sql').read_text(),prepare=False)
            tables=db.execute("SELECT tablename FROM pg_tables WHERE schemaname='huanchang'").fetchall()
            db.execute('TRUNCATE '+','.join('huanchang."'+t[0]+'"' for t in tables))
        self.storage=StorageDouble();self.app=create_app(database=lambda:connect(URL),storage=self.storage,origin='http://localhost',testing=True)
        self.a=Client(self.app);self.b=Client(self.app);self.g=Client(self.app)
        self.register=self.a.ok('auth/register',{'handle':'cloud_a','name':'作者A','password':'cloud-test-password','email':'a@example.test'})
        self.b.ok('auth/register',{'handle':'cloud_b','name':'读者B','password':'cloud-test-password'})
        self.au=self.a.get('state').json['viewer'];self.bu=self.b.get('state').json['viewer']
    def publish(self,title='云上故事',**fields):
        import huanchang_server as hc
        value={'id':hc.ident(),'kind':'作品','form':'文字','title':title,'body':'从一个梦开始。','files':[],'role':'故事设计'};value.update(fields)
        return self.a.ok('works',value)['id']
    def test_accounts_privacy_csrf_and_session_across_instances(self):
        state=self.a.get('state').json;self.assertEqual(state['account']['email'],'a@example.test');self.assertNotIn('a@example.test',json.dumps(self.b.get('state').json))
        self.assertEqual(self.g.post('draft',{'key':'publish','data':{}}).status_code,401)
        self.assertEqual(self.a.client.post('/api/draft',json={'key':'publish','data':{}},headers={'X-CSRF-Token':'bad'}).status_code,403)
        other=create_app(database=lambda:connect(URL),storage=self.storage,origin='http://localhost',testing=True);cookie=self.a.client.get_cookie('huanchang_cloud_test')
        client=other.test_client();client.set_cookie(cookie.key,cookie.value);self.assertEqual(client.get('/api/state').json['viewer'],self.au)
        self.a.ok('auth/logout',{});self.assertIsNone(client.get('/api/state').json['viewer'])
    def test_publishing_revision_drafts_search_and_pagination(self):
        ids=[self.publish('云上故事'+str(i)) for i in range(28)]
        state=self.a.get('state?view=works&kind=作品').json;self.assertEqual(state['page']['total'],28);self.assertEqual(len(state['page']['ids']),24)
        self.assertEqual(len(self.a.get('state?view=works&kind=作品&offset=24').json['page']['ids']),4)
        self.assertEqual(self.a.get('state?view=works&query=云上故事0').json['page']['total'],1)
        self.a.ok('draft',{'key':'publish','data':{'title':'私有草稿'}});self.assertEqual(self.a.get('state').json['drafts']['publish']['title'],'私有草稿');self.assertEqual(self.b.get('state').json['drafts'],{})
        self.assertEqual(self.b.post('work-status',{'id':ids[0],'action':'delete'}).status_code,403)
    def test_signed_upload_ownership_integrity_and_media_access(self):
        ticket=self.a.ok('upload/prepare',{'name':'画面.png','mime':'image/png','size':8});fid=ticket['id'];self.storage.objects[fid]=b'\x89PNG\r\n\x1a\n'
        self.assertEqual(self.b.post('upload/complete',{'id':fid}).status_code,403)
        item=self.a.ok('upload/complete',{'id':fid});self.assertEqual(self.a.ok('upload/complete',{'id':fid}),item)
        self.assertEqual(self.g.client.get(item['url']).status_code,404)
        self.assertEqual(self.a.client.get(item['url']).status_code,302)
        wid=self.publish(files=[item],form='图像');self.assertEqual(self.g.client.get(item['url']).status_code,302)
        self.assertEqual(self.a.post('upload/prepare',{'name':'x.png','mime':'image/png','size':21*1024*1024}).status_code,413)
        bad=self.a.ok('upload/prepare',{'name':'bad.png','mime':'image/png','size':8});self.storage.objects[bad['id']]=b'notimage';self.assertEqual(self.a.post('upload/complete',{'id':bad['id']}).status_code,400)
    def test_reactions_metrics_comments_and_private_message(self):
        wid=self.publish();self.b.ok('reaction',{'id':wid,'kind':'likes','enabled':True});self.b.ok('reaction',{'id':wid,'kind':'saved','enabled':True})
        self.b.ok('view',{'id':wid});self.b.ok('view',{'id':wid});owner=self.a.get('state').json['ownerStats'][wid];self.assertEqual(owner,{'views':1,'likes':1,'saved':1})
        import huanchang_server as hc
        self.b.ok('comment',{'id':hc.ident(),'work':wid,'body':'我想了解这个故事。'})
        self.b.ok('message',{'id':hc.ident(),'peer':self.au,'body':'一起交流故事吧。','work':wid})
        self.a.ok('message',{'id':hc.ident(),'peer':self.bu,'body':'可以，先聊聊方向。','accept':True})
        threads=self.a.get('state?view=messages&peer='+self.bu).json['threads'];self.assertEqual(len(threads[0]['messages']),2)
        self.assertEqual(self.g.get('messages?thread='+threads[0]['key']).status_code,401)
    def test_recovery_revokes_sessions_and_demo_off(self):
        self.g.ok('auth/recover',{'handle':'cloud_a','code':self.register['recoveryCode'],'password':'new-cloud-password'})
        self.assertIsNone(self.a.get('state').json['viewer']);self.assertEqual(self.g.post('auth/demo',{'id':'linjian'}).status_code,403)
        self.a.ok('auth/login',{'handle':'cloud_a','password':'new-cloud-password'})
    def test_database_schema_private_and_cloud_not_local_fallback(self):
        with connect(URL) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM pg_tables WHERE schemaname='huanchang' AND rowsecurity=false").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT has_schema_privilege('public','huanchang','USAGE')").fetchone()[0],False)
        with patch.dict(os.environ,{'HUANCHANG_DATABASE_URL':'','HUANCHANG_PUBLIC_ORIGIN':''}):app=create_app()
        self.assertEqual(app.test_client().get('/api/state').status_code,503)
    def test_rate_limits_shared_and_forwarded_header_not_trusted(self):
        import huanchang_server as hc
        # Avoid crossing a fixed-minute rate window during a real-time test.
        with patch('huanchang_server.now',return_value=hc.now()):
            for i in range(12):self.g.post('auth/login',{'handle':'missing','password':'cloud-test-password'})
            r=self.g.client.post('/api/auth/login',json={'handle':'missing','password':'cloud-test-password'},headers={'X-Forwarded-For':'8.8.8.8'})
        self.assertEqual(r.status_code,429)
    def test_remote_tls_config_rejects_duplicate_override(self):
        for url in ['postgresql://example.test/postgres?sslmode=disable','postgresql://example.test/postgres?sslmode=require&sslmode=disable','http://example.test/postgres?sslmode=require']:
            with patch.dict(os.environ,{'HUANCHANG_DATABASE_URL':url,'VERCEL_ENV':''}),patch('cloud.runtime.Storage',return_value=self.storage):
                app=create_app(origin='https://example.test')
            self.assertIsNone(app.extensions['huanchang_runtime'])
    def test_invalid_text_cannot_poison_json_search(self):
        import huanchang_server as hc
        self.assertEqual(self.a.post('works',{'id':hc.ident(),'title':'标题','body':'不支持\0的字符','form':'文字','files':[]}).status_code,400)
        self.assertEqual(self.a.post('profile',{'name':'名字','directions':['\ud800']}).status_code,400)
        self.assertEqual(self.a.get('state?query=标题').status_code,200)
    def test_labeled_examples_cannot_receive_contact(self):
        from scripts.cloud_seed_demo import seed_examples
        with connect(URL) as db:seed_examples(db)
        state=self.a.get('state?view=works').json
        self.assertTrue(all(w['sample'] for w in state['works']))
        self.assertFalse(state['users']['linjian']['discoverable'])
        self.assertFalse(state['users']['linjian']['available'])
        self.assertEqual(self.a.post('message',{'peer':'linjian','body':'请联系'}).status_code,400)
