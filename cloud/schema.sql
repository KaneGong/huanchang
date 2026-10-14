-- Apply once administratively; never expose this schema through Supabase APIs.
CREATE SCHEMA IF NOT EXISTS huanchang;
REVOKE ALL ON SCHEMA huanchang FROM PUBLIC;
SET search_path TO huanchang,pg_catalog;
CREATE TABLE IF NOT EXISTS users(id TEXT PRIMARY KEY,handle TEXT UNIQUE,password TEXT,salt TEXT,recovery TEXT,data TEXT NOT NULL,role TEXT DEFAULT 'member',disabled INTEGER DEFAULT 0,demo INTEGER DEFAULT 0,email TEXT NOT NULL DEFAULT '',email_verified INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS sessions(token TEXT PRIMARY KEY,actor TEXT,csrf TEXT,expires BIGINT);
CREATE TABLE IF NOT EXISTS works(id TEXT PRIMARY KEY,author TEXT,data TEXT NOT NULL,deleted INTEGER DEFAULT 0,hidden INTEGER DEFAULT 0,created BIGINT,updated BIGINT,revision INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS comments(id TEXT PRIMARY KEY,author TEXT,work TEXT,body TEXT,parent TEXT,deleted INTEGER DEFAULT 0,pinned INTEGER DEFAULT 0,hidden INTEGER DEFAULT 0,created BIGINT);
CREATE TABLE IF NOT EXISTS threads(id TEXT PRIMARY KEY,a TEXT,b TEXT,initiator TEXT,status TEXT,updated BIGINT,UNIQUE(a,b));
CREATE TABLE IF NOT EXISTS messages(id TEXT PRIMARY KEY,thread TEXT,sender TEXT,body TEXT,work TEXT,created BIGINT);
CREATE TABLE IF NOT EXISTS receipts(actor TEXT,thread TEXT,seen BIGINT,PRIMARY KEY(actor,thread));
CREATE TABLE IF NOT EXISTS work_metrics(work TEXT PRIMARY KEY,views BIGINT NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS work_view_visitors(work TEXT,visitor TEXT,seen BIGINT,PRIMARY KEY(work,visitor));
CREATE TABLE IF NOT EXISTS reactions(actor TEXT,work TEXT,kind TEXT,PRIMARY KEY(actor,work,kind));
CREATE TABLE IF NOT EXISTS blocks(actor TEXT,peer TEXT,PRIMARY KEY(actor,peer));
CREATE TABLE IF NOT EXISTS notifications(id TEXT PRIMARY KEY,actor TEXT,sender TEXT,kind TEXT,target TEXT,body TEXT,seen INTEGER DEFAULT 0,created BIGINT,comment_id TEXT);
CREATE TABLE IF NOT EXISTS reports(id TEXT PRIMARY KEY,reporter TEXT,kind TEXT,target TEXT,reason TEXT,status TEXT DEFAULT 'pending',resolution TEXT DEFAULT '',created BIGINT,UNIQUE(reporter,kind,target));
CREATE TABLE IF NOT EXISTS uploads(id TEXT PRIMARY KEY,owner TEXT,mime TEXT,name TEXT,size BIGINT,created BIGINT);
CREATE TABLE IF NOT EXISTS drafts(actor TEXT,key TEXT,data TEXT,updated BIGINT,PRIMARY KEY(actor,key));
CREATE TABLE IF NOT EXISTS email_challenges(actor TEXT,purpose TEXT,email TEXT,code TEXT,expires BIGINT,attempts INTEGER DEFAULT 0,created BIGINT,PRIMARY KEY(actor,purpose));
CREATE TABLE IF NOT EXISTS upload_reservations(id TEXT PRIMARY KEY,owner TEXT,mime TEXT,name TEXT,size BIGINT,expires BIGINT);
CREATE TABLE IF NOT EXISTS rate_windows(scope TEXT,kind TEXT,window_id BIGINT,total INTEGER,PRIMARY KEY(scope,kind,window_id));
CREATE UNIQUE INDEX IF NOT EXISTS verified_email_unique ON users(lower(email)) WHERE email_verified=1 AND email!='';
CREATE INDEX IF NOT EXISTS works_listing ON works(deleted,hidden,created DESC,id);
CREATE INDEX IF NOT EXISTS works_author ON works(author,created DESC);
CREATE INDEX IF NOT EXISTS messages_thread_time ON messages(thread,created,id);
CREATE INDEX IF NOT EXISTS comments_work ON comments(work,created);
CREATE INDEX IF NOT EXISTS notifications_actor ON notifications(actor,created DESC);
CREATE INDEX IF NOT EXISTS upload_reservations_owner ON upload_reservations(owner,expires);
DO $$ DECLARE t RECORD; BEGIN
 FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='huanchang' LOOP
  EXECUTE format('ALTER TABLE huanchang.%I ENABLE ROW LEVEL SECURITY',t.tablename);
  EXECUTE format('REVOKE ALL ON TABLE huanchang.%I FROM PUBLIC',t.tablename);
 END LOOP;
END $$;
DO $$ DECLARE r TEXT; t RECORD; BEGIN
 FOREACH r IN ARRAY ARRAY['anon','authenticated'] LOOP
  IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname=r) THEN
   EXECUTE format('REVOKE ALL ON SCHEMA huanchang FROM %I',r);
   FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='huanchang' LOOP
    EXECUTE format('REVOKE ALL ON TABLE huanchang.%I FROM %I',t.tablename,r);
   END LOOP;
  END IF;
 END LOOP;
END $$;
