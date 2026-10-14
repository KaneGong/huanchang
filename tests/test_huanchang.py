from contextlib import closing
import base64
import http.cookiejar
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
import huanchang_server as hc

class Client:
    def __init__(self,base):
        self.base=base;self.csrf='';self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def request(self,path,data=None,extra=None,raw=False):
        headers={'X-CSRF-Token':self.csrf}
        if data is not None:headers['Content-Type']='image/png' if raw else 'application/json'
        if extra:headers.update(extra)
        req=urllib.request.Request(self.base+'/api/'+path,data=data if raw else json.dumps(data).encode() if data is not None else None,headers=headers)
        try:
            with self.opener.open(req) as r:payload=r.read();status=r.status
        except urllib.error.HTTPError as e:payload=e.read();status=e.code;e.close()
        try:value=json.loads(payload)
        except ValueError:value=payload
        if isinstance(value,dict) and value.get('csrf'):self.csrf=value['csrf']
        return status,value
    def post(self,path,data):
        status,value=self.request(path,data);assert status==200,(path,status,value);return value
    def state(self):return self.request('state')[1]

class FoundationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();root=Path(self.temp.name)
        self.server=hc.make_server(0,root/'huanchang.sqlite3',root/'media',demo=False)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start();self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.a=Client(self.base);self.b=Client(self.base);self.c=Client(self.base)
        self.ra=self.a.post('auth/register',{'handle':'author_a','name':'创作者A','password':'test-password-a'})
        self.b.post('auth/register',{'handle':'reader_b','name':'读者B','password':'test-password-b'})
        self.c.post('auth/register',{'handle':'other_c','name':'其他人C','password':'test-password-c'})
        self.au=self.a.state()['viewer'];self.bu=self.b.state()['viewer'];self.cu=self.c.state()['viewer']
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.temp.cleanup()
    def publish(self,client=None,**fields):
        p={'id':hc.ident(),'title':'测试文字作品','body':'一个具体故事。','kind':'作品','form':'文字','files':[],'role':'故事设定与修改','process':'比较两个开场后选择第二种。','lookingFor':'想听听节奏的反馈。'};p.update(fields)
        return (client or self.a).post('works',p)['id']
    def test_optional_registration_email_validation_persistence_and_privacy(self):
        registered=Client(self.base)
        registered.post('auth/register',{'handle':'with_email','name':'邮箱测试','password':'test-email-password','email':'  Artist+demo@EXAMPLE.COM  '})
        state=registered.state();uid=state['viewer']
        self.assertEqual(state['account']['email'],'Artist+demo@example.com')
        self.assertNotIn('email',state['users'][uid])
        self.assertNotIn('email',self.a.state()['users'][uid])
        self.assertIsNone(Client(self.base).state()['account'])
        self.assertNotIn('Artist+demo@example.com',json.dumps(self.b.state()))
        registered.post('profile',{**state['users'][uid],'bio':'保存公开主页','email':'attacker@example.com'})
        self.assertEqual(registered.state()['account']['email'],'Artist+demo@example.com')
        registered.post('auth/logout',{})
        registered.post('auth/login',{'handle':'with_email','password':'test-email-password'})
        self.assertEqual(registered.state()['account']['email'],'Artist+demo@example.com')
        hc.initialize(self.server.db_path)
        with closing(sqlite3.connect(self.server.db_path)) as db, db:
            self.assertEqual(db.execute('SELECT email FROM users WHERE id=?',(uid,)).fetchone()[0],'Artist+demo@example.com')
        self.assertEqual(self.a.state()['account']['email'],'')
        empty=Client(self.base);empty.post('auth/register',{'handle':'blank_email','name':'留空测试','password':'test-email-password','email':'   '})
        self.assertEqual(empty.state()['account']['email'],'')
        duplicate=Client(self.base);duplicate.post('auth/register',{'handle':'same_email','name':'未验证信息','password':'test-email-password','email':'Artist+demo@example.com'})
        # Unverified optional email is not an account identifier.
        self.assertEqual(duplicate.state()['account']['email'],'Artist+demo@example.com')
        invalid=['not-an-email','a@@example.com','a b@example.com','.a@example.com','a..b@example.com','a@example','a@-example.com','a'*65+'@example.com','a'*250+'@example.com',None,7]
        for value in invalid:
            # This checks field validation; auth rate limiting has separate coverage.
            with self.server.rate_lock:self.server.rates.clear()
            status,_=Client(self.base).request('auth/register',{'handle':'invalid_email','name':'无效邮箱','password':'test-email-password','email':value})
            self.assertEqual(status,400,value)
        with closing(sqlite3.connect(self.server.db_path)) as db, db:
            self.assertIsNone(db.execute('SELECT id FROM users WHERE handle=?',('invalid_email',)).fetchone())

    def test_optional_email_migration_preserves_legacy_accounts(self):
        path=Path(self.temp.name)/'legacy.sqlite3'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("CREATE TABLE users(id TEXT PRIMARY KEY,handle TEXT UNIQUE,password TEXT,salt TEXT,recovery TEXT,data TEXT NOT NULL,role TEXT DEFAULT 'member',disabled INTEGER DEFAULT 0,demo INTEGER DEFAULT 0)")
            db.execute('INSERT INTO users VALUES(?,?,?,?,?,?,?,?,?)',('legacy','legacy','hash','salt','recovery',json.dumps({'name':'原有用户'},ensure_ascii=False),'member',0,0))
            before=db.execute('SELECT * FROM users').fetchall()
        hc.initialize(path)
        with closing(sqlite3.connect(path)) as db, db:
            self.assertEqual(db.execute('SELECT id,handle,password,salt,recovery,data,role,disabled,demo FROM users').fetchall(),before)
            self.assertEqual(db.execute('SELECT email FROM users').fetchone()[0],'')
            db.execute('UPDATE users SET email=?',('legacy@example.com',))
        hc.initialize(path)
        with closing(sqlite3.connect(path)) as db, db:
            self.assertEqual(db.execute('SELECT email FROM users').fetchone()[0],'legacy@example.com')

    def test_account_login_recovery_and_session_revocation(self):
        self.assertNotIn('password',json.dumps(self.a.state()))
        old=Client(self.base);old.post('auth/login',{'handle':'author_a','password':'test-password-a'})
        out=self.c.post('auth/recover',{'handle':'author_a','password':'new-password-a','code':self.ra['recoveryCode']})
        self.assertIn('recoveryCode',out);self.assertIsNone(old.state()['viewer']);self.assertIsNone(self.a.state()['viewer'])
        self.assertEqual(Client(self.base).request('auth/recover',{'handle':'author_a','password':'other-password','code':self.ra['recoveryCode']})[0],401)
    def test_backup_and_health_check(self):
        self.publish()
        with self.a.opener.open(self.base+'/healthz') as response:self.assertEqual(response.status,200)
        result=subprocess.run([sys.executable,str(hc.ROOT/'scripts/huanchang_backup.py'),'--data',str(Path(self.temp.name)),'--out',str(Path(self.temp.name)/'backups')],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        backup=next((Path(self.temp.name)/'backups').glob('*/huanchang.sqlite3'))
        with closing(sqlite3.connect(backup)) as db:
            self.assertEqual(db.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0],0);self.assertEqual(db.execute('SELECT count(*) FROM works').fetchone()[0],10)
        self.assertEqual(self.a.state()['viewer'],self.au)
    def test_demo_auth_is_off_without_flag(self):
        self.assertEqual(Client(self.base).request('auth/demo',{'id':'linjian'})[0],403)
    def test_guest_csrf_and_cross_origin_rejected(self):
        self.assertEqual(Client(self.base).request('draft',{'key':'publish','data':{}})[0],401)
        self.assertEqual(self.a.request('draft',{'key':'publish','data':{}},extra={'X-CSRF-Token':'bad'})[0],403)
        self.assertEqual(self.a.request('draft',{'key':'publish','data':{}},extra={'Origin':'https://other.example'})[0],403)
        self.assertEqual(self.a.request('draft',{'key':'publish','data':{}},extra={'Host':'attacker.example'})[0],403)
        self.server.public_origin='https://preview.example'
        self.assertEqual(self.a.request('draft',{'key':'publish','data':{}},extra={'Host':'preview.example','Origin':'https://preview.example'})[0],200)
        self.assertEqual(self.a.request('draft',{'key':'publish','data':{}},extra={'Host':'preview.example','Origin':'https://other.example'})[0],403)
        self.assertEqual(self.a.request('draft',{'key':'publish','data':{}},extra={'Host':'other.example','Origin':'https://preview.example'})[0],403)
    def test_works_ownership_edit_revision_delete_and_restore(self):
        wid=self.publish();w=next(x for x in self.a.state()['works'] if x['id']==wid)
        payload={**w,'title':'更新标题','topic':'文字','revision':w['revision']}
        self.assertEqual(self.b.request('works',payload)[0],403)
        self.a.post('works',payload);self.assertEqual(self.a.request('works',payload)[0],409)
        self.assertEqual(self.b.request('work-status',{'id':wid,'action':'delete'})[0],403)
        self.a.post('work-status',{'id':wid,'action':'delete'});self.assertFalse(any(x['id']==wid for x in self.b.state()['works']))
        self.assertTrue(any(x['id']==wid for x in self.a.state()['deletedWorks']))
        self.a.post('work-status',{'id':wid,'action':'restore'});self.assertTrue(any(x['id']==wid for x in self.b.state()['works']))
    def test_tutorials_and_drafts_are_owned_and_persist(self):
        self.a.post('draft',{'key':'publish','data':{'title':'我的草稿'}})
        self.assertNotIn('publish',self.b.state()['drafts']);self.assertEqual(self.a.state()['drafts']['publish']['title'],'我的草稿')
        wid=self.publish(kind='教程与经验',draftKey='publish');self.assertEqual(next(x for x in self.b.state()['works'] if x['id']==wid)['kind'],'教程与经验');self.assertNotIn('publish',self.a.state()['drafts'])
        with closing(sqlite3.connect(self.server.db_path)) as d:self.assertTrue(d.execute('SELECT 1 FROM works WHERE id=?',(wid,)).fetchone())
    def test_first_message_request_participant_access_and_duplicates(self):
        out=self.b.post('message',{'peer':self.au,'body':'想交流剧本','work':'last-train','id':'message_1'});tid=out['thread']
        self.b.post('message',{'peer':self.au,'body':'重复提交','id':'message_1'})
        self.assertEqual(len(self.a.state()['threads'][0]['messages']),1);self.assertEqual(self.c.state()['threads'],[])
        self.assertEqual(self.b.request('message',{'peer':self.au,'body':'再次催促'})[0],409)
        self.assertEqual(self.b.request('thread-action',{'id':tid,'action':'accept'})[0],403)
        self.assertEqual(self.c.request('thread-action',{'id':tid,'action':'read'})[0],404)
        self.a.post('thread-action',{'id':tid,'action':'accept'});self.a.post('message',{'peer':self.bu,'body':'我们可以交流。'})
        self.assertEqual(len(self.b.state()['threads'][0]['messages']),2)
    def test_read_is_per_user_and_list_does_not_clear(self):
        tid=self.b.post('message',{'peer':self.au,'body':'一个想法'})['thread']
        self.assertFalse(self.a.state()['threads'][0]['messages'][0]['seen']);self.a.state();self.assertFalse(self.a.state()['threads'][0]['messages'][0]['seen'])
        self.a.post('thread-action',{'id':tid,'action':'read'});self.assertTrue(self.a.state()['threads'][0]['messages'][0]['seen'])
        self.a.post('thread-action',{'id':tid,'action':'accept'});self.a.post('message',{'peer':self.bu,'body':'收到。'})
        self.assertFalse(self.b.state()['threads'][0]['messages'][-1]['seen'])
    def test_close_and_withdrawal_rules(self):
        tid=self.b.post('message',{'peer':self.au,'body':'请求'})['thread']
        self.assertEqual(self.a.request('thread-action',{'id':tid,'action':'withdraw'})[0],403)
        self.b.post('thread-action',{'id':tid,'action':'withdraw'});self.b.post('message',{'peer':self.au,'body':'新的完整请求'})
        self.a.post('thread-action',{'id':tid,'action':'close'});self.assertEqual(self.b.request('message',{'peer':self.au,'body':'再次催促'})[0],409)

    def test_first_reply_accepts_atomically_and_preserves_retry(self):
        tid=self.b.post('message',{'peer':self.au,'body':'请教一个问题','id':'first'})['thread']
        self.assertEqual(self.b.request('message',{'peer':self.au,'body':'绕过等待','accept':True})[0],409)
        self.assertEqual(self.a.request('message',{'peer':self.bu,'body':'','accept':True})[0],400)
        self.assertEqual(self.a.state()['threads'][0]['status'],'pending')
        self.assertEqual(self.a.request('message',{'peer':self.bu,'body':'不应单独发送'})[0],409)
        payload={'peer':self.bu,'body':'可以先聊这个想法','accept':True,'id':'first-reply'}
        self.a.post('message',payload);self.a.post('message',payload)
        t=self.b.state()['threads'][0]
        self.assertEqual(t['status'],'accepted');self.assertEqual(len(t['messages']),2)
        self.b.post('message',{'peer':self.au,'body':'继续讨论'});self.assertEqual(len(self.a.state()['threads'][0]['messages']),3)

    def test_only_recipient_can_reopen_and_block_still_applies(self):
        tid=self.b.post('message',{'peer':self.au,'body':'第一次联系'})['thread']
        self.a.post('thread-action',{'id':tid,'action':'close'})
        self.assertEqual(self.b.request('thread-action',{'id':tid,'action':'reopen'})[0],403)
        self.assertEqual(self.c.request('thread-action',{'id':tid,'action':'reopen'})[0],404)
        self.a.post('block',{'peer':self.bu,'enabled':True})
        self.assertEqual(self.a.request('thread-action',{'id':tid,'action':'reopen'})[0],403)
        self.a.post('block',{'peer':self.bu,'enabled':False});self.a.post('thread-action',{'id':tid,'action':'reopen'})
        self.assertEqual(self.b.state()['threads'][0]['status'],'accepted')
        self.b.post('message',{'peer':self.au,'body':'继续交流'})
        self.assertEqual(len(self.a.state()['threads'][0]['messages']),2)

    def test_creator_discovery_requires_choice_profile_and_content(self):
        u=self.a.state()['users'][self.au]
        self.assertFalse(u['available']);self.assertFalse(u['collaborationSet']);self.assertFalse(u['discoverable'])
        self.publish();u=self.a.state()['users'][self.au];self.assertFalse(u['discoverable'])
        u.update(bio='用文字构建故事',creatorListed=True);self.a.post('profile',u)
        u=self.b.state()['users'][self.au];self.assertTrue(u['discoverable']);self.assertFalse(u['collaborationSet'])
        own=self.a.state()['users'][self.au];own.update(creatorListed=False,available=True,collaborationSet=True)
        self.a.post('profile',own);u=self.b.state()['users'][self.au]
        self.assertFalse(u['discoverable']);self.assertTrue(u['available']);self.assertTrue(u['collaborationSet'])

    def test_comment_notice_has_exact_destination_and_reply_draft_clears(self):
        wid=self.publish();cid=self.b.post('comment',{'work':wid,'body':'第一条问题'})['id']
        self.a.post('draft',{'key':'comment:'+wid,'data':{'body':'另一个主评论草稿'}})
        self.a.post('draft',{'key':'reply:'+wid+':'+cid,'data':{'body':'回复草稿'}})
        child=self.a.post('comment',{'work':wid,'parent':cid,'body':'回答第一条问题'})['id']
        notice=next(n for n in self.b.state()['notifications'] if n['kind']=='comment')
        self.assertEqual(notice['target'],wid);self.assertEqual(notice['comment_id'],child)
        self.assertIn('comment:'+wid,self.a.state()['drafts']);self.assertNotIn('reply:'+wid+':'+cid,self.a.state()['drafts'])

    def test_old_notifications_migrate_without_rewriting_records(self):
        target=Path(self.temp.name)/'legacy.sqlite3'
        with closing(sqlite3.connect(target)) as db,db:
            db.execute('CREATE TABLE notifications(id TEXT PRIMARY KEY,actor TEXT,sender TEXT,kind TEXT,target TEXT,body TEXT,seen INTEGER DEFAULT 0,created INTEGER)')
            db.execute('INSERT INTO notifications VALUES(?,?,?,?,?,?,?,?)',('old','linjian','qinghe','comment','forest','原有通知',0,123))
        hc.initialize(target);hc.initialize(target)
        with closing(sqlite3.connect(target)) as db:
            row=db.execute('SELECT * FROM notifications WHERE id=?',('old',)).fetchone()
            self.assertEqual(row[:8],('old','linjian','qinghe','comment','forest','原有通知',0,123));self.assertIsNone(row[8])
    def test_comments_notification_pin_and_tombstone(self):
        wid=self.publish();cid=self.b.post('comment',{'work':wid,'body':'怎样确定开场？'})['id']
        child=self.a.post('comment',{'work':wid,'body':'先确定情绪。','parent':cid})['id']
        self.assertTrue(any(n['kind']=='comment' for n in self.a.state()['notifications']))
        self.assertTrue(any(n['kind']=='comment' for n in self.b.state()['notifications']))
        self.assertEqual(self.b.request('comment-action',{'id':cid,'action':'pin'})[0],403)
        self.a.post('comment-action',{'id':cid,'action':'pin'});self.b.post('comment-action',{'id':cid,'action':'delete'})
        c=self.a.state()['comments'];self.assertTrue(next(x for x in c if x['id']==cid)['deleted']);self.assertEqual(next(x for x in c if x['id']==child)['body'],'先确定情绪。')
    def test_block_blocks_both_directions_and_unblock_restores(self):
        self.a.post('block',{'peer':self.bu,'enabled':True})
        self.assertEqual(self.b.request('message',{'peer':self.au,'body':'不应发送'})[0],403)
        self.assertEqual(self.a.request('message',{'peer':self.bu,'body':'不应发送'})[0],403)
        wid=self.publish();self.assertEqual(self.b.request('comment',{'work':wid,'body':'不应评论'})[0],403)
        self.a.post('block',{'peer':self.bu,'enabled':False});self.b.post('message',{'peer':self.au,'body':'可以发送'})
    def test_upload_ownership_and_draft_media_private_until_published(self):
        blob=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1kAAAAASUVORK5CYII=')
        status,f=self.a.request('upload',blob,raw=True);self.assertEqual(status,200)
        self.assertEqual(self.b.request('media/'+f['id'])[0],404)
        self.assertEqual(self.b.request('works',{'title':'盗用','body':'说明','form':'图像','files':[f]})[0],403)
        wid=self.publish(form='图像',files=[f]);self.assertEqual(self.b.request('media/'+f['id'])[0],200)
        self.a.post('work-status',{'id':wid,'action':'delete'});self.assertEqual(self.b.request('media/'+f['id'])[0],404)
    def test_invalid_media_and_external_links_rejected(self):
        self.assertEqual(self.a.request('upload',b'not an image',raw=True)[0],400)
        self.assertEqual(self.a.request('works',{'title':'视频','body':'说明','form':'视频','url':'javascript:alert(1)','files':[]})[0],400)
        self.assertEqual(self.a.request('works',{'title':'图片','body':'说明','form':'图像','files':[]})[0],400)
    def test_profile_representative_works_and_availability(self):
        wid=self.publish();p=self.a.state()['users'][self.au];p.update(available=False,bio='我的方向',directions=['文字叙事'],featured=[wid]);self.a.post('profile',p)
        u=self.b.state()['users'][self.au];self.assertFalse(u['available']);self.assertEqual(u['featured'],[wid])
        bp=self.b.state()['users'][self.bu];bp['featured']=[wid];self.assertEqual(self.b.request('profile',bp)[0],400)
    def test_private_bookmarks_and_server_moderation(self):
        wid=self.publish();self.a.post('reaction',{'id':wid,'kind':'saved','enabled':True});self.assertIn(wid,self.a.state()['saved']);self.assertNotIn(wid,self.b.state()['saved'])
        self.b.post('report',{'kind':'work','target':wid,'reason':'需要复核'});self.b.post('report',{'kind':'work','target':wid,'reason':'重复举报'})
        self.assertEqual(self.a.request('admin')[0],403)
        with closing(sqlite3.connect(self.server.db_path)) as db, db:db.execute("UPDATE users SET role='admin' WHERE id=?",(self.cu,))
        queue=self.c.request('admin')[1];self.assertEqual(len(queue['reports']),1);rid=queue['reports'][0]['id']
        self.c.post('admin-action',{'action':'resolve','id':rid,'decision':'hide','note':'已下架复核'})
        self.assertFalse(any(w['id']==wid for w in self.a.state()['works']));self.assertEqual(self.a.request('work-status',{'id':wid,'action':'restore'})[0],403)
        self.assertTrue(any(n['kind']=='report' for n in self.b.state()['notifications']))
        self.c.post('admin-action',{'action':'restore-work','id':wid});self.assertTrue(any(w['id']==wid for w in self.a.state()['works']))

    def test_profile_crop_source_ownership_and_private_metadata(self):
        image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aC1sAAAAASUVORK5CYII=')
        original=self.a.request('upload',image,raw=True)[1]['url']
        crop=self.a.request('upload',image,raw=True)[1]['url']
        p=self.a.state()['users'][self.au];p.update(avatar=crop,avatarEdit={'source':original,'cx':.5,'cy':.5,'zoom':2})
        self.a.post('profile',p)
        owned=self.a.state()['users'][self.au]
        self.assertEqual(owned['avatarEdit']['source'],original)
        self.assertNotIn('avatarEdit',self.b.state()['users'][self.au])
        self.assertEqual(self.b.request('media/'+original.split('/')[-1])[0],404)
        self.assertEqual(self.b.request('media/'+crop.split('/')[-1])[0],200)
        foreign=self.b.request('upload',image,raw=True)[1]['url']
        owned['avatarEdit']['source']=foreign
        self.assertEqual(self.a.request('profile',owned)[0],403)
        owned['avatarEdit']['source']=original;owned['avatarEdit']['zoom']=float('nan')
        self.assertEqual(self.a.request('profile',owned)[0],400)
        owned['avatarEdit']['zoom']=2;owned.update(avatar='',avatarEdit=None)
        self.a.post('profile',owned)
        self.assertIsNone(self.a.state()['users'][self.au]['avatarEdit'])

    def test_work_cover_is_independent_of_order_and_restores_metadata(self):
        image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1kAAAAASUVORK5CYII=')
        first=self.a.request('upload',image,raw=True)[1]
        second=self.a.request('upload',image,raw=True)[1]
        crop=self.a.request('upload',image,raw=True)[1]
        first.update(caption='角色设定',width=600,height=1400)
        cover={**crop,'sourceId':first['id'],'cx':.4,'cy':.6,'zoom':2,'ratio':1.32}
        wid=self.publish(form='图像',files=[first,second],cover=cover)
        w=next(x for x in self.a.state()['works'] if x['id']==wid)
        self.assertEqual(w['image'],crop['url']);self.assertEqual(w['files'][0]['caption'],'角色设定')
        self.assertEqual(w['files'][0]['height'],1400)
        self.a.post('works',{**w,'files':list(reversed(w['files']))})
        w=next(x for x in self.b.state()['works'] if x['id']==wid)
        self.assertEqual(w['image'],crop['url']);self.assertEqual(w['images'][0],second['url'])
        self.assertEqual(w['cover']['sourceId'],first['id'])
        self.assertEqual(self.b.request('media/'+crop['id'])[0],200)
        self.a.post('work-status',{'id':wid,'action':'delete'})
        self.assertEqual(self.b.request('media/'+crop['id'])[0],404)

    def test_work_cover_membership_ownership_and_invalid_metadata(self):
        image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aX1kAAAAASUVORK5CYII=')
        f=self.a.request('upload',image,raw=True)[1];foreign=self.b.request('upload',image,raw=True)[1]
        data={'title':'图片','body':'说明','form':'图像','files':[f],'cover':{'sourceId':f['id'],'id':foreign['id']}}
        self.assertEqual(self.a.request('works',data)[0],403)
        data['cover']={'sourceId':foreign['id'],'id':f['id']}
        self.assertEqual(self.a.request('works',data)[0],400)
        data['cover']={'sourceId':f['id'],'id':f['id'],'zoom':float('nan')}
        self.assertEqual(self.a.request('works',data)[0],400)
        data['cover']['zoom']=10**400
        self.assertEqual(self.a.request('works',data)[0],400)
        data['cover']['zoom']=1;data['files']=[{**f,'caption':'长'*161}]
        self.assertEqual(self.a.request('works',data)[0],400)
        data.pop('cover');data['files']=[f]
        out=self.a.post('works',data);w=next(x for x in self.a.state()['works'] if x['id']==out['id'])
        self.assertEqual(w['image'],f['url']);self.assertIsNone(w['cover'])

    def test_profile_revision_and_duplicate_comment_protection(self):
        p=self.a.state()['users'][self.au];self.a.post('profile',p)
        self.assertEqual(self.a.request('profile',p)[0],409)
        wid=self.publish();payload={'work':wid,'body':'同一条讨论','id':'stable-comment-id'}
        self.b.post('comment',payload);self.b.post('comment',payload)
        self.assertEqual(len([c for c in self.a.state()['comments'] if c['id']=='stable-comment-id']),1)
        self.assertEqual(self.c.request('comment',payload)[0],409)
    def test_notification_read_owned_and_blocked_request_acceptance(self):
        tid=self.b.post('message',{'peer':self.au,'body':'请求'})['thread'];n=self.a.state()['notifications'][0]
        self.b.post('notifications',{'id':n['id']});self.assertFalse(self.a.state()['notifications'][0]['seen'])
        self.a.post('block',{'peer':self.bu,'enabled':True});self.assertEqual(self.b.request('thread-action',{'id':tid,'action':'accept'})[0],403)
        self.a.post('notifications',{'id':n['id']});self.assertTrue(self.a.state()['notifications'][0]['seen'])


    def test_owner_metrics_are_private_and_reactions_are_current(self):
        wid=self.publish()
        for _ in range(2):self.b.post('reaction',{'id':wid,'kind':'likes','enabled':True})
        self.b.post('reaction',{'id':wid,'kind':'saved','enabled':True})
        self.c.post('reaction',{'id':wid,'kind':'likes','enabled':True})
        self.assertEqual(self.a.state()['ownerStats'][wid],{'likes':2,'saved':1,'views':0})
        self.assertNotIn(wid,self.b.state()['ownerStats'])
        self.assertEqual(Client(self.base).state()['ownerStats'],{})
        self.assertNotIn('ownerStats',self.b.state()['works'][0])
        self.assertNotIn(wid,self.c.state()['saved'])
        self.b.post('reaction',{'id':wid,'kind':'likes','enabled':False})
        self.b.post('reaction',{'id':wid,'kind':'saved','enabled':False})
        self.assertEqual(self.a.state()['ownerStats'][wid],{'likes':1,'saved':0,'views':0})
        self.a.post('work-status',{'id':wid,'action':'delete'})
        self.assertNotIn(wid,self.a.state()['ownerStats'])
        self.a.post('work-status',{'id':wid,'action':'restore'})
        self.assertEqual(self.a.state()['ownerStats'][wid]['likes'],1)

    def test_views_deduplicate_exclude_owner_and_persist(self):
        wid=self.publish()
        self.assertFalse(self.a.post('view',{'id':wid})['counted'])
        self.assertTrue(self.b.post('view',{'id':wid})['counted'])
        self.assertFalse(self.b.post('view',{'id':wid})['counted'])
        again=Client(self.base);again.post('auth/login',{'handle':'reader_b','password':'test-password-b'})
        self.assertFalse(again.post('view',{'id':wid})['counted'])
        anonymous=Client(self.base)
        self.assertTrue(anonymous.post('view',{'id':wid})['counted'])
        self.assertFalse(anonymous.post('view',{'id':wid})['counted'])
        self.assertTrue(Client(self.base).post('view',{'id':wid})['counted'])
        self.assertEqual(self.a.state()['ownerStats'][wid]['views'],3)
        with closing(sqlite3.connect(self.server.db_path)) as db,db:
            db.execute('UPDATE work_view_visitors SET seen=? WHERE visitor=?',(hc.now()-1800001,hc.digest('account:'+self.bu)))
        self.assertTrue(self.b.post('view',{'id':wid})['counted'])
        hc.initialize(self.server.db_path)
        self.assertEqual(self.a.state()['ownerStats'][wid]['views'],4)
        self.assertEqual(self.b.request('view',{'id':wid},{'X-CSRF-Token':'bad'})[0],403)
        self.assertEqual(self.b.request('view',{'id':wid},{'Origin':'https://other.example'})[0],403)

    def test_unavailable_and_blocked_works_do_not_gain_views(self):
        wid=self.publish()
        self.assertEqual(self.b.request('view',{'id':'missing'})[0],404)
        self.a.post('block',{'peer':self.bu,'enabled':True})
        self.assertEqual(self.b.request('view',{'id':wid})[0],404)
        self.a.post('block',{'peer':self.bu,'enabled':False})
        self.a.post('work-status',{'id':wid,'action':'delete'})
        self.assertEqual(Client(self.base).request('view',{'id':wid})[0],404)
        self.a.post('work-status',{'id':wid,'action':'restore'})
        with closing(sqlite3.connect(self.server.db_path)) as db,db:db.execute('UPDATE users SET disabled=1 WHERE id=?',(self.au,))
        self.assertEqual(Client(self.base).request('view',{'id':wid})[0],404)
        with closing(sqlite3.connect(self.server.db_path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM work_metrics WHERE work=?',(wid,)).fetchone()[0],0)

if __name__=='__main__':unittest.main()
