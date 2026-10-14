from contextlib import closing
import json
import queue
import re
import sqlite3
import unittest
import threading
from urllib.parse import urlencode
from tests.test_huanchang import FoundationTests, Client
import huanchang_server as hc
from hc_readiness import client_ip, proxy_networks

class ReadinessTests(unittest.TestCase):
    setUp=FoundationTests.setUp
    tearDown=FoundationTests.tearDown
    publish=FoundationTests.publish
    def mail_setup(self):
        self.mail=queue.Queue();self.server.mail_sender=lambda to,subject,body:self.mail.put((to,subject,body));self.server.mail_enabled=True
        self.a.post('account/email',{'email':'author@example.com','password':'test-password-a'})
    def code(self):return re.search(r'：([0-9]{8})',self.mail.get(timeout=3)[2]).group(1)
    def verify(self):
        self.a.post('account/email-request',{});self.a.post('account/email-verify',{'code':self.code()})
    def clear_rates(self):
        with self.server.rate_lock:self.server.rates.clear()
    def test_pagination_filters_counts_and_direct_links(self):
        ids=[self.publish(title='分页作品'+str(i),topic='角色',body='独立分页验证') for i in range(55)]
        first=self.a.request('state?'+urlencode({'author':self.au,'kind':'作品'}))[1]
        self.assertEqual(first['page']['total'],55);self.assertEqual(len(first['page']['ids']),24);self.assertTrue(first['page']['hasMore'])
        second=self.a.request('state?'+urlencode({'author':self.au,'offset':24}))[1]
        third=self.a.request('state?'+urlencode({'author':self.au,'offset':48}))[1]
        got=first['page']['ids']+second['page']['ids']+third['page']['ids'];self.assertEqual(set(got),set(ids));self.assertEqual(len(got),len(set(got)))
        self.assertFalse(third['page']['hasMore'])
        exact=self.b.request('state?'+urlencode({'query':'分页作品54','kind':'作品'}))[1];self.assertEqual(exact['page']['total'],1)
        direct=self.b.request('state?'+urlencode({'id':ids[0]}))[1];self.assertIn(ids[0],[w['id'] for w in direct['works']])
        self.b.post('block',{'peer':self.au,'enabled':True})
        hidden=self.b.request('state?'+urlencode({'author':self.au,'id':ids[0]}))[1];self.assertEqual(hidden['page']['total'],0);self.assertNotIn(ids[0],[w['id'] for w in hidden['works']])
    def test_private_counts_cover_all_pages(self):
        ids=[self.publish(title='收藏'+str(i)) for i in range(30)]
        for wid in ids:self.b.post('reaction',{'id':wid,'kind':'saved','enabled':True})
        state=self.b.request('state?personal=saved')[1];self.assertEqual(state['privateCounts']['saved'],30);self.assertEqual(state['page']['total'],30)
        self.assertEqual(len(state['page']['ids']),24);self.assertEqual(self.a.state()['privateCounts']['saved'],0)
    def test_message_history_is_bounded_ordered_and_private(self):
        self.a.post('message',{'peer':self.bu,'body':'首条消息','id':hc.ident()});self.b.post('message',{'peer':self.au,'body':'回复并开始','id':hc.ident(),'accept':True})
        tid=self.a.state()['threads'][0]['key']
        with closing(sqlite3.connect(self.server.db_path)) as db,db:
            db.executemany('INSERT INTO messages VALUES(?,?,?,?,?,?)',[(f'history-{i:03d}',tid,self.au,'历史'+str(i),None,hc.now()+i+100) for i in range(93)])
        state=self.b.request('state?view=messages&peer='+self.au)[1];thread=state['threads'][0];self.assertEqual(len(thread['messages']),40);self.assertTrue(thread['hasOlder']);self.assertEqual(thread['unread'],94)
        before=thread['messages'][0]['id'];older=self.b.request('messages?thread='+tid+'&before='+before)[1]
        self.assertEqual(len(older['messages']),40);self.assertFalse(set(m['id'] for m in older['messages'])&set(m['id'] for m in thread['messages']))
        self.assertEqual(self.c.request('messages?thread='+tid)[0],404);self.assertEqual(Client(self.base).request('messages?thread='+tid)[0],401)
        preview=self.b.request('state?view=works')[1]['threads'][0];self.assertEqual(len(preview['messages']),1);self.assertEqual(preview['unread'],94)
    def test_untrusted_forwarded_headers_do_not_bypass_limits(self):
        for i in range(12):self.a.request('auth/login',{'handle':'missing','password':'wrong'},{'X-Forwarded-For':'198.51.100.'+str(i)})
        self.assertEqual(self.a.request('auth/login',{'handle':'missing','password':'wrong'},{'X-Forwarded-For':'203.0.113.1'})[0],429)
    def test_trusted_proxy_clients_are_independent(self):
        self.server.trusted_proxies=proxy_networks('127.0.0.1/32');self.clear_rates()
        for i in range(12):self.a.request('auth/login',{'handle':'missing','password':'wrong'},{'X-Forwarded-For':'198.51.100.1'})
        self.assertEqual(self.a.request('auth/login',{'handle':'missing','password':'wrong'},{'X-Forwarded-For':'198.51.100.1'})[0],429)
        self.assertEqual(self.b.request('auth/login',{'handle':'missing','password':'wrong'},{'X-Forwarded-For':'198.51.100.2'})[0],401)
        self.assertEqual(client_ip('127.0.0.1',{'X-Forwarded-For':'evil, 198.51.100.2'},self.server.trusted_proxies),'127.0.0.1')
    def test_email_verification_reset_and_session_revocation(self):
        self.mail_setup();self.verify();self.assertEqual(self.a.state()['account']['email_verified'],1)
        self.assertNotIn('author@example.com',json.dumps(self.b.state()))
        old=Client(self.base);old.post('auth/login',{'handle':'author_a','password':'test-password-a'})
        reset=Client(self.base);reset.post('auth/email-request',{'handle':'author_a','email':'author@example.com'});code=self.code()
        out=reset.post('auth/email-reset',{'handle':'author_a','email':'author@example.com','code':code,'password':'new-password-a'})
        self.assertTrue(out['recoveryCode']);self.assertIsNone(old.state()['viewer']);self.assertIsNone(self.a.state()['viewer'])
        self.assertEqual(reset.request('auth/email-reset',{'handle':'author_a','email':'author@example.com','code':code,'password':'new-password-b'})[0],400)
        self.assertEqual(Client(self.base).request('auth/login',{'handle':'author_a','password':'test-password-a'})[0],401)
    def test_unverified_email_and_missing_account_have_same_response(self):
        self.mail_setup();a=Client(self.base)
        real=a.post('auth/email-request',{'handle':'author_a','email':'author@example.com'})
        missing=a.post('auth/email-request',{'handle':'missing_a','email':'author@example.com'})
        self.assertEqual(real,missing);self.assertTrue(self.mail.empty())
        self.assertEqual(self.a.request('account/email',{'email':'other@example.com','password':'wrong'})[0],400)
        self.assertEqual(self.a.request('account/email',{'email':None,'password':'test-password-a'})[0],400)
    def test_email_expiry_attempt_limit_reissue_and_uniqueness(self):
        self.mail_setup();self.a.post('account/email-request',{});code=self.code()
        for i in range(5):self.assertEqual(self.a.request('account/email-verify',{'code':'wrong'})[0],400)
        self.assertEqual(self.a.request('account/email-verify',{'code':code})[0],400)
        self.clear_rates();self.a.post('account/email-request',{});new=self.code()
        with closing(sqlite3.connect(self.server.db_path)) as db,db:db.execute("UPDATE email_challenges SET expires=0 WHERE actor=?",(self.au,))
        self.assertEqual(self.a.request('account/email-verify',{'code':new})[0],400)
        self.a.post('account/email-request',{});self.a.post('account/email-verify',{'code':self.code()})
        self.assertEqual(self.b.request('account/email',{'email':'AUTHOR@example.com','password':'test-password-b'})[0],409)
    def test_mail_disabled_and_recovery_code_rotation(self):
        self.assertEqual(Client(self.base).request('auth/email-request',{'handle':'author_a','email':'a@example.com'})[0],503)
        updated=self.a.post('account/recovery-code',{'password':'test-password-a'})
        self.assertEqual(Client(self.base).request('auth/recover',{'handle':'author_a','code':self.ra['recoveryCode'],'password':'updated-password'})[0],401)
        self.assertEqual(Client(self.base).request('auth/recover',{'handle':'author_a','code':updated['recoveryCode'],'password':'updated-password'})[0],200)

    def test_recovery_code_also_invalidates_pending_email_reset(self):
        self.mail_setup();self.verify();requester=Client(self.base);requester.post('auth/email-request',{'handle':'author_a','email':'author@example.com'});pending=self.code()
        requester.post('auth/recover',{'handle':'author_a','code':self.ra['recoveryCode'],'password':'recovery-updated-password'})
        self.assertEqual(requester.request('auth/email-reset',{'handle':'author_a','email':'author@example.com','code':pending,'password':'another-new-password'})[0],400)

    def test_content_management_paginates_published_and_private_trash(self):
        ids=[self.publish(title='内容管理'+str(i)) for i in range(55)]
        first=self.a.request('state?view=studio&author=self')[1];self.assertEqual(first['page']['total'],55);self.assertEqual(len(first['page']['ids']),24)
        with closing(sqlite3.connect(self.server.db_path)) as db,db:db.execute('UPDATE works SET deleted=1 WHERE author=?',(self.au,))
        pages=[self.a.request('state?view=studio&author=self&trash=1&offset='+str(offset))[1] for offset in [0,24,48]]
        self.assertEqual([len(s['deletedWorks']) for s in pages],[24,24,7]);self.assertEqual(len(set(w['id'] for s in pages for w in s['deletedWorks'])),55)
        foreign=self.b.request('state?view=studio&author='+self.au+'&trash=1')[1];self.assertEqual(foreign['deletedWorks'],[])
        self.assertEqual(Client(self.base).request('state?view=studio&author='+self.au+'&trash=1')[1]['deletedWorks'],[])

    def test_disabling_demo_revokes_existing_demo_sessions_and_contact(self):
        self.server.demo=True;demo=Client(self.base);demo.post('auth/demo',{'id':'linjian'});self.assertEqual(demo.state()['viewer'],'linjian')
        self.server.demo=False;self.assertIsNone(demo.state()['viewer']);self.assertFalse(self.a.state()['users']['linjian']['discoverable'])
        self.assertEqual(demo.request('draft',{'key':'publish','data':{'title':'cannot write'}})[0],401)
        self.assertEqual(self.a.request('message',{'id':hc.ident(),'peer':'linjian','body':'not a real user'})[0],400)

    def test_restore_into_new_directory_preserves_content_media_and_login(self):
        import subprocess,sys,base64
        from scripts.huanchang_restore import restore
        from pathlib import Path
        wid=self.publish();root=Path(self.temp.name)
        image=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+j2T8AAAAASUVORK5CYII=')
        status,value=self.a.request('upload',image,{'X-File-Name':'test.png'},raw=True);self.assertEqual(status,200)
        self.mail_setup();self.a.post('account/email-request',{});self.code()
        subprocess.run([sys.executable,'scripts/huanchang_backup.py','--data',str(root),'--out',str(root/'backups')],check=True,capture_output=True)
        backup=next((root/'backups').iterdir());target=root/'restored';restore(backup,target)
        with closing(sqlite3.connect(target/'huanchang.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0],0);self.assertEqual(db.execute('SELECT count(*) FROM email_challenges').fetchone()[0],0)
        self.assertEqual((target/'media'/value['id']).read_bytes(),image)
        with self.assertRaises(ValueError):restore(backup,target)
        server=hc.make_server(0,target/'huanchang.sqlite3',target/'media');worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            client=Client('http://127.0.0.1:'+str(server.server_port));client.post('auth/login',{'handle':'author_a','password':'test-password-a'});state=client.state();self.assertEqual(state['viewer'],self.au);self.assertIn(wid,[w['id'] for w in state['works']])
        finally:server.shutdown();server.server_close()

class DeliveryTests(unittest.TestCase):
    def test_public_listener_rejects_demo_or_missing_https(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            db=Path(folder)/'not-created.sqlite3'
            for options in [{},{'public_origin':'https://example.test','secure_cookie':True,'demo':True},{'public_origin':'http://example.test','secure_cookie':True}]:
                with self.assertRaises(ValueError):hc.make_server(0,db,host='0.0.0.0',**options)
            self.assertFalse(db.exists())

    def test_smtp_uses_tls_and_formats_transactional_email(self):
        from unittest.mock import patch,MagicMock
        import hc_readiness
        with patch.dict('os.environ',{'HUANCHANG_SMTP_HOST':'smtp.example.test','HUANCHANG_MAIL_FROM':'hello@example.test','HUANCHANG_SMTP_USER':'sender','HUANCHANG_SMTP_PASSWORD':'test-only','HUANCHANG_SMTP_TLS':'starttls'},clear=True),patch('hc_readiness.smtplib.SMTP') as connect:
            transport=MagicMock();connect.return_value=transport
            delivery=hc_readiness.SMTPDelivery();self.assertTrue(delivery.enabled);delivery('reader@example.test','幻场 · 验证邮箱','测试投递正文')
            transport.starttls.assert_called_once();transport.login.assert_called_once_with('sender','test-only');transport.send_message.assert_called_once()
            self.assertEqual(transport.send_message.call_args.args[0]['To'],'reader@example.test')

if __name__=='__main__':unittest.main()
