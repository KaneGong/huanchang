"""Bounded reads, trusted proxy handling, and configurable transactional email.
No email is sent unless a sender is explicitly configured.
"""
import hashlib
import ipaddress
import os
import smtplib
import ssl
from email.message import EmailMessage
from urllib.parse import parse_qs, urlsplit

PAGE_SIZE=24

def proxy_networks(value):
    return tuple(ipaddress.ip_network(v.strip(),strict=False) for v in value.split(',') if v.strip())

def client_ip(peer,headers,networks):
    address=ipaddress.ip_address(peer)
    def trusted(a):return any(a in n for n in networks)
    if not trusted(address):return str(address)
    values=headers.get('X-Forwarded-For','').split(',')
    if len(values)>16:return str(address)
    try:chain=[ipaddress.ip_address(v.strip()) for v in values if v.strip()]
    except ValueError:return str(address)
    for a in reversed(chain):
        address=a
        if not trusted(a):break
    return str(address)

def params(path):return {k:v[-1] for k,v in parse_qs(urlsplit(path).query).items()}
def number(q,key,default,maximum):
    try:return max(0,min(maximum,int(q.get(key,default))))
    except (ValueError,TypeError):return default

def work_page(db,uid,q):
    trash=q.get('view')=='studio' and q.get('trash')=='1' and bool(uid)
    where=['(w.deleted=1 OR w.hidden=1)' if trash else 'w.deleted=0', 'u.disabled=0'];args=[]
    if trash:where.append('w.author=?');args.append(uid)
    else:where.append('w.hidden=0')
    if uid:where.append('NOT EXISTS(SELECT 1 FROM blocks b WHERE b.actor=? AND b.peer=w.author)');args.append(uid)
    kind=q.get('kind')
    if kind in ['作品','教程与经验']:where.append("json_extract(w.data,'$.kind')=?");args.append(kind)
    if q.get('author'):where.append('w.author=?');args.append(q['author'])
    if q.get('form') in ['图像','视频','声音','文字','互动','3D']:where.append("json_extract(w.data,'$.form')=?");args.append(q['form'])
    if q.get('personal') in ['saved','likes']:
        where.append('EXISTS(SELECT 1 FROM reactions r WHERE r.work=w.id AND r.actor=? AND r.kind=?)');args.extend([uid or '',q['personal']])
    theme=q.get('theme')
    if theme=='world':where.append("(json_extract(w.data,'$.tags') LIKE '%角色%' OR json_extract(w.data,'$.tags') LIKE '%世界观%')")
    if theme=='story':where.append("json_extract(w.data,'$.form') IN ('文字','视频','声音')")
    if theme=='senses':where.append("(json_extract(w.data,'$.form') IN ('3D','互动') OR json_extract(w.data,'$.tags') LIKE '%自然%' OR json_extract(w.data,'$.tags') LIKE '%材质%' OR json_extract(w.data,'$.tags') LIKE '%空间%')")
    search=q.get('query','').strip()[:200].lower()
    if search:
        where.append("instr(lower(coalesce(json_extract(w.data,'$.title'),'')||' '||coalesce(json_extract(w.data,'$.body'),'')||' '||coalesce(json_extract(w.data,'$.role'),'')||' '||coalesce(json_extract(w.data,'$.process'),'')||' '||coalesce(json_extract(w.data,'$.tools'),'')||' '||coalesce(json_extract(w.data,'$.tags'),'')||' '||coalesce(json_extract(u.data,'$.name'),'')),?)>0");args.append(search)
    source=' FROM works w JOIN users u ON u.id=w.author WHERE '+' AND '.join(where)
    total=db.execute('SELECT count(*)'+source,args).fetchone()[0]
    offset=number(q,'offset',0,1000000)
    order="CASE WHEN coalesce(json_extract(w.data,'$.curatedReason'),'')!='' THEN 0 ELSE 1 END," if q.get('sort','精选')=='精选' else ''
    rows=db.execute('SELECT w.*'+source+' ORDER BY '+order+'w.created DESC,w.id DESC LIMIT ? OFFSET ?',[*args,PAGE_SIZE,offset]).fetchall()
    return rows,{'offset':offset,'total':total,'hasMore':offset+len(rows)<total,'nextOffset':offset+len(rows),'ids':[r['id'] for r in rows]}

class SMTPDelivery:
    def __init__(self):
        self.host=os.environ.get('HUANCHANG_SMTP_HOST','');self.port=int(os.environ.get('HUANCHANG_SMTP_PORT','587'))
        self.sender=os.environ.get('HUANCHANG_MAIL_FROM','');self.user=os.environ.get('HUANCHANG_SMTP_USER','');self.password=os.environ.get('HUANCHANG_SMTP_PASSWORD','')
        self.tls=os.environ.get('HUANCHANG_SMTP_TLS','starttls')
        self.enabled=bool(self.host and self.sender)
        if self.tls not in ['starttls','ssl']:raise ValueError('SMTP必须使用starttls或ssl')
        if self.enabled and ('\r' in self.sender or '\n' in self.sender or '@' not in self.sender):raise ValueError('发信地址格式错误')
    def __call__(self,recipient,subject,body):
        message=EmailMessage();message['From']=self.sender;message['To']=recipient;message['Subject']=subject;message.set_content(body)
        context=ssl.create_default_context()
        if self.tls=='ssl':smtp=smtplib.SMTP_SSL(self.host,self.port,timeout=10,context=context)
        else:smtp=smtplib.SMTP(self.host,self.port,timeout=10)
        with smtp:
            if self.tls=='starttls':smtp.starttls(context=context)
            if self.user:smtp.login(self.user,self.password)
            smtp.send_message(message)
