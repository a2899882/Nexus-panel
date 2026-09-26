#!/usr/bin/env python3
"""Nexus-panel control plane: Python standard library only."""
import argparse
import base64
import hashlib
import hmac
import http.cookies
import http.server
import ipaddress
import json
import os
import re
import secrets
import socket
import sqlite3
import sys
import threading
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.getenv('NEXUS_DB', str(ROOT / 'data' / 'panel.db')))
WEB = ROOT / 'web'
LOCK = threading.RLock()
USERNAME_RE = re.compile(r'^[A-Za-z0-9_.-]{3,32}$')
HOST_RE = re.compile(r'^[A-Za-z0-9.-]{1,253}$')

def now(): return int(time.time())
def digest(token): return hashlib.sha256(token.encode()).hexdigest()
def password_hash(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    raw = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 260000)
    return 'pbkdf2$' + base64.b64encode(salt).decode() + '$' + base64.b64encode(raw).decode()
def verify_password(password, stored):
    try:
        _, salt, target = stored.split('$')
        actual = password_hash(password, base64.b64decode(salt))
        return hmac.compare_digest(actual, stored)
    except (ValueError, TypeError): return False

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=10000')
    return c

def init_db():
    with LOCK, connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, password TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS nodes(id INTEGER PRIMARY KEY, name TEXT NOT NULL, address TEXT NOT NULL DEFAULT '', grp TEXT NOT NULL DEFAULT '', token_hash TEXT UNIQUE NOT NULL, last_seen INTEGER NOT NULL DEFAULT 0, version TEXT NOT NULL DEFAULT '', applied_revision INTEGER NOT NULL DEFAULT 0, error TEXT NOT NULL DEFAULT '', realm_running INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS rules(id INTEGER PRIMARY KEY, node_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE, name TEXT NOT NULL, grp TEXT NOT NULL DEFAULT '', listen_host TEXT NOT NULL DEFAULT '0.0.0.0', listen_port INTEGER NOT NULL, remote_host TEXT NOT NULL, remote_port INTEGER NOT NULL, protocol TEXT NOT NULL CHECK(protocol IN ('tcp','udp','both')), extra_remotes TEXT NOT NULL DEFAULT '[]', balance TEXT NOT NULL DEFAULT 'off', enabled INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_rules_node ON rules(node_id);
        CREATE INDEX IF NOT EXISTS idx_events_time ON events(created_at);
        ''')
        columns={r['name'] for r in c.execute('PRAGMA table_info(rules)')}
        if 'extra_remotes' not in columns: c.execute("ALTER TABLE rules ADD COLUMN extra_remotes TEXT NOT NULL DEFAULT '[]'")
        if 'balance' not in columns: c.execute("ALTER TABLE rules ADD COLUMN balance TEXT NOT NULL DEFAULT 'off'")
        c.execute("INSERT OR IGNORE INTO settings VALUES ('revision','1')")
        c.execute("INSERT OR IGNORE INTO settings VALUES ('retention_days','30')")
        c.execute('DELETE FROM sessions WHERE expires<?', (now(),))
        c.execute('DELETE FROM events WHERE created_at<?', (now()-30*86400,))

def event(c, actor, action, detail=''):
    c.execute('INSERT INTO events(created_at,actor,action,detail) VALUES(?,?,?,?)', (now(), actor, action, detail[:200]))
    days = int(c.execute("SELECT value FROM settings WHERE key='retention_days'").fetchone()[0])
    c.execute('DELETE FROM events WHERE created_at<?', (now()-days*86400,))
def bump(c):
    c.execute("UPDATE settings SET value=CAST(value AS INTEGER)+1 WHERE key='revision'")
def revision(c): return int(c.execute("SELECT value FROM settings WHERE key='revision'").fetchone()[0])
def fail(message, status=400): raise APIError(message, status)
class APIError(Exception):
    def __init__(self, message, status=400): super().__init__(message); self.status=status

def required_string(data, key, limit=100):
    value = data.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit: fail(f'{key} 不能为空或过长')
    return value.strip()
def port(data, key):
    value = data.get(key)
    if isinstance(value, bool) or not str(value).isdigit() or not 1 <= int(value) <= 65535: fail(f'{key} 必须是 1–65535')
    return int(value)
def valid_host(value, *, bind=False):
    if value in ('0.0.0.0', '::') and bind: return value
    try:
        ipaddress.ip_address(value)
        return value
    except ValueError: pass
    if not bind and HOST_RE.fullmatch(value) and '..' not in value and not value.startswith(('.', '-')) and not value.endswith(('.', '-')):
        return value
    fail('地址格式无效；监听地址必须是 IP，目标可用 IP 或域名')
def addr(host, p): return f'[{host}]:{p}' if ':' in host else f'{host}:{p}'
def parse_remote(value):
    if not isinstance(value,str) or len(value)>280: fail('附加目标格式无效')
    try:
        parsed=urllib.parse.urlsplit('tcp://'+value.strip())
        host=valid_host(parsed.hostname or '')
        p=parsed.port
        if not p or parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password: raise ValueError()
        return addr(host,p)
    except (ValueError,APIError): fail('附加目标需为 域名:端口 或 [IPv6]:端口')

def rule_data(data):
    name = required_string(data,'name',80)
    listen_host = valid_host(str(data.get('listen_host') or '0.0.0.0'), bind=True)
    remote_host = valid_host(required_string(data,'remote_host',253))
    protocol = data.get('protocol')
    if protocol not in ('tcp','udp','both'): fail('协议必须是 TCP、UDP 或两者')
    enabled = data.get('enabled', True)
    if not isinstance(enabled, bool): fail('enabled 必须是布尔值')
    grp = str(data.get('grp') or '').strip()
    if len(grp)>60: fail('分组过长')
    extras=data.get('extra_remotes') or []
    if isinstance(extras,str): extras=[x.strip() for x in extras.replace(',','\n').splitlines() if x.strip()]
    if not isinstance(extras,list) or len(extras)>8: fail('附加目标最多 8 个')
    extras=[parse_remote(x) for x in extras]
    if len(set(extras))!=len(extras) or addr(remote_host,port(data,'remote_port')) in extras: fail('目标地址重复')
    balance=data.get('balance') or 'off'
    if balance not in ('off','roundrobin','iphash') or (extras and balance=='off') or (not extras and balance!='off'):
        fail('多个目标须选择轮询或 IP 哈希策略')
    return dict(name=name, grp=grp, listen_host=listen_host, listen_port=port(data,'listen_port'), remote_host=remote_host, remote_port=port(data,'remote_port'), protocol=protocol, extra_remotes=json.dumps(extras), balance=balance, enabled=int(enabled))
def conflict(c, node_id, r, ignore=0):
    # Overlapping TCP/UDP modes on a node cannot bind the same port and address.
    rows = c.execute('SELECT * FROM rules WHERE node_id=? AND id!=?',(node_id,ignore)).fetchall()
    for row in rows:
        if row['listen_port']!=r['listen_port']: continue
        if row['listen_host']!=r['listen_host'] and row['listen_host'] not in ('0.0.0.0','::') and r['listen_host'] not in ('0.0.0.0','::'): continue
        if row['protocol']=='both' or r['protocol']=='both' or row['protocol']==r['protocol']:
            fail(f'端口 {r["listen_port"]} 与现有规则「{row["name"]}」冲突',409)

def clean_node(row):
    return {k:row[k] for k in ('id','name','address','grp','last_seen','version','applied_revision','error','realm_running')}
def clean_rule(row):
    result=dict(row); result['extra_remotes']=json.loads(result['extra_remotes']); return result
def cleanup(c):
    days = int(c.execute("SELECT value FROM settings WHERE key='retention_days'").fetchone()[0])
    c.execute('DELETE FROM events WHERE created_at<?',(now()-days*86400,))
    c.execute('DELETE FROM sessions WHERE expires<?',(now(),))

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = 'Nexus-panel/0.1'
    def log_message(self, fmt, *args):
        if not self.path.startswith('/api/agent/'):
            sys.stderr.write('%s %s\n' % (self.address_string(), fmt % args))
    def send_bytes(self, status, payload, content_type='application/json', cookie=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length',str(len(payload)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('X-Frame-Options','DENY')
        self.send_header('Referrer-Policy','same-origin')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
        if cookie: self.send_header('Set-Cookie', cookie)
        self.end_headers(); self.wfile.write(payload)
    def reply(self, data, status=200, cookie=None):
        self.send_bytes(status,json.dumps(data,ensure_ascii=False,separators=(',',':')).encode(),cookie=cookie)
    def body(self):
        length = int(self.headers.get('Content-Length','0'))
        if length<1 or length>65536: fail('请求体大小无效',413)
        try:
            data=json.loads(self.rfile.read(length))
            if not isinstance(data,dict): raise ValueError()
            return data
        except (ValueError,UnicodeError): fail('JSON 格式无效')
    def cookie_header(self, token='', max_age=0):
        secure = '; Secure' if os.getenv('NEXUS_COOKIE_SECURE','0')=='1' else ''
        return f'np_session={token}; HttpOnly; SameSite=Strict; Path=/; Max-Age={max_age}{secure}'
    def auth(self,c):
        raw=http.cookies.SimpleCookie()
        try: raw.load(self.headers.get('Cookie',''))
        except http.cookies.CookieError: pass
        token=raw.get('np_session')
        if not token: fail('请先登录',401)
        row=c.execute('SELECT users.id,users.username FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.token_hash=? AND sessions.expires>?',(digest(token.value),now())).fetchone()
        if not row: fail('登录已失效',401)
        return row
    def agent_auth(self,c):
        auth=self.headers.get('Authorization','')
        if not auth.startswith('Bearer ') or len(auth)>160: fail('节点凭证无效',401)
        node=c.execute('SELECT * FROM nodes WHERE token_hash=?',(digest(auth[7:]),)).fetchone()
        if not node: fail('节点凭证无效',401)
        return node
    def csrf(self):
        if self.headers.get('X-Nexus-Request')!='panel': fail('请求验证失败',403)
        origin=self.headers.get('Origin')
        if origin:
            host=self.headers.get('Host','')
            if urllib.parse.urlparse(origin).netloc!=host: fail('来源验证失败',403)
    def dispatch(self):
        parsed=urllib.parse.urlsplit(self.path); path=parsed.path; method=self.command
        if method=='GET' and not path.startswith('/api/'):
            name='index.html' if path=='/' else path.lstrip('/')
            if name not in ('index.html','app.js','styles.css'): fail('未找到',404)
            kind={'index.html':'text/html; charset=utf-8','app.js':'text/javascript; charset=utf-8','styles.css':'text/css; charset=utf-8'}[name]
            return self.send_bytes(200,(WEB/name).read_bytes(),kind)
        if not path.startswith('/api/'): fail('未找到',404)
        data=self.body() if method in ('POST','PUT','PATCH') else {}
        if path.startswith('/api/agent/'):
            with LOCK, connect() as c:
                node=self.agent_auth(c)
                if path=='/api/agent/poll' and method=='POST':
                    rules=c.execute('SELECT * FROM rules WHERE node_id=? AND enabled=1 ORDER BY id',(node['id'],)).fetchall()
                    desired=[{'id':r['id'],'listen':addr(r['listen_host'],r['listen_port']),'remote':addr(r['remote_host'],r['remote_port']),'protocol':r['protocol'],'extra_remotes':json.loads(r['extra_remotes']),'balance':r['balance']} for r in rules]
                    c.execute('UPDATE nodes SET last_seen=?,version=?,applied_revision=?,error=?,realm_running=? WHERE id=?', (now(),str(data.get('version',''))[:32],int(data.get('applied_revision') or 0),str(data.get('error',''))[:240],int(bool(data.get('realm_running'))),node['id']))
                    return self.reply({'revision':revision(c),'rules':desired})
                fail('未找到',404)
        if path=='/api/login' and method=='POST':
            self.csrf()
            user=str(data.get('username',''))[:80]; password=str(data.get('password',''))
            # Constant cost for unknown names; an in-memory window throttles guessing.
            key=self.client_address[0]; recent=[t for t in self.server.login_attempts.get(key,[]) if t>now()-600]
            if len(recent)>=10: fail('尝试过于频繁，请稍后再试',429)
            with LOCK, connect() as c:
                row=c.execute('SELECT * FROM users WHERE username=?',(user,)).fetchone()
                if not verify_password(password,row['password'] if row else self.server.dummy_hash):
                    self.server.login_attempts[key]=recent+[now()]; fail('账号或密码错误',401)
                self.server.login_attempts.pop(key,None)
                token=secrets.token_urlsafe(40)
                c.execute('INSERT INTO sessions VALUES(?,?,?)',(digest(token),row['id'],now()+7*86400))
                return self.reply({'ok':True},cookie=self.cookie_header(token,7*86400))
        with LOCK, connect() as c:
            user=self.auth(c)
            if method!='GET': self.csrf()
            if path=='/api/me' and method=='GET': return self.reply({'username':user['username']})
            if path=='/api/logout' and method=='POST':
                raw=http.cookies.SimpleCookie(); raw.load(self.headers.get('Cookie',''))
                if raw.get('np_session'): c.execute('DELETE FROM sessions WHERE token_hash=?',(digest(raw['np_session'].value),))
                return self.reply({'ok':True},cookie=self.cookie_header())
            if path=='/api/dashboard' and method=='GET':
                nodes=c.execute('SELECT * FROM nodes ORDER BY id DESC').fetchall()
                rules=c.execute('SELECT * FROM rules ORDER BY id DESC').fetchall()
                events=c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 20').fetchall()
                return self.reply({'nodes':[clean_node(n) for n in nodes], 'rules':[clean_rule(r) for r in rules], 'events':[dict(e) for e in events], 'revision':revision(c), 'retention_days':int(c.execute("SELECT value FROM settings WHERE key='retention_days'").fetchone()[0])})
            if path=='/api/nodes' and method=='POST':
                name=required_string(data,'name',80); address=str(data.get('address') or '').strip(); grp=str(data.get('grp') or '').strip()
                if len(address)>253 or len(grp)>60: fail('地址或分组过长')
                if address: valid_host(address)
                token=secrets.token_urlsafe(40)
                cur=c.execute('INSERT INTO nodes(name,address,grp,token_hash) VALUES(?,?,?,?)',(name,address,grp,digest(token)))
                event(c,user['username'],'添加服务器',name); bump(c)
                return self.reply({'id':cur.lastrowid,'token':token},201)
            if path.startswith('/api/nodes/'):
                tail=path.split('/')[3:]
                if not tail or not tail[0].isdigit(): fail('未找到',404)
                nid=int(tail[0]); node=c.execute('SELECT * FROM nodes WHERE id=?',(nid,)).fetchone()
                if not node: fail('服务器不存在',404)
                if len(tail)==2 and tail[1]=='token' and method=='POST':
                    token=secrets.token_urlsafe(40)
                    c.execute('UPDATE nodes SET token_hash=?, last_seen=0 WHERE id=?',(digest(token),nid))
                    event(c,user['username'],'重置节点密钥',node['name'])
                    return self.reply({'token':token})
                if len(tail)==1 and method=='PUT':
                    name=required_string(data,'name',80); address=str(data.get('address') or '').strip(); grp=str(data.get('grp') or '').strip()
                    if len(address)>253 or len(grp)>60: fail('地址或分组过长')
                    if address: valid_host(address)
                    c.execute('UPDATE nodes SET name=?,address=?,grp=? WHERE id=?',(name,address,grp,nid))
                    event(c,user['username'],'编辑服务器',name)
                    return self.reply({'ok':True})
                if len(tail)==1 and method=='DELETE':
                    if c.execute('SELECT 1 FROM rules WHERE node_id=? LIMIT 1',(nid,)).fetchone():
                        fail('请先删除该服务器的所有规则，并等待节点应用空配置',409)
                    if node['last_seen'] and node['applied_revision']!=revision(c):
                        fail('节点尚未确认规则已清空；请等待同步，离线节点需先在服务器停用代理',409)
                    c.execute('DELETE FROM nodes WHERE id=?',(nid,)); bump(c)
                    event(c,user['username'],'删除服务器',node['name'])
                    return self.reply({'ok':True})
            if path=='/api/rules' and method=='POST':
                nid=data.get('node_id')
                if isinstance(nid,bool) or not str(nid).isdigit() or not c.execute('SELECT 1 FROM nodes WHERE id=?',(int(nid),)).fetchone(): fail('请选择服务器')
                r=rule_data(data); conflict(c,int(nid),r)
                cur=c.execute('INSERT INTO rules(node_id,name,grp,listen_host,listen_port,remote_host,remote_port,protocol,extra_remotes,balance,enabled,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(int(nid),*r.values(),now()))
                bump(c); event(c,user['username'],'添加规则',r['name'])
                return self.reply({'id':cur.lastrowid},201)
            if path.startswith('/api/rules/'):
                tail=path.split('/')[3:]
                if len(tail)!=1 or not tail[0].isdigit(): fail('未找到',404)
                rid=int(tail[0]); old=c.execute('SELECT * FROM rules WHERE id=?',(rid,)).fetchone()
                if not old: fail('规则不存在',404)
                if method=='PUT':
                    r=rule_data(data); conflict(c,old['node_id'],r,rid)
                    c.execute('UPDATE rules SET name=?,grp=?,listen_host=?,listen_port=?,remote_host=?,remote_port=?,protocol=?,extra_remotes=?,balance=?,enabled=? WHERE id=?',(*r.values(),rid))
                    bump(c); event(c,user['username'],'编辑规则',r['name']); return self.reply({'ok':True})
                if method=='DELETE':
                    c.execute('DELETE FROM rules WHERE id=?',(rid,)); bump(c)
                    event(c,user['username'],'删除规则',old['name']); return self.reply({'ok':True})
            if path=='/api/password' and method=='POST':
                current=str(data.get('current','')); new=str(data.get('password',''))
                stored=c.execute('SELECT password FROM users WHERE id=?',(user['id'],)).fetchone()['password']
                if not verify_password(current,stored): fail('当前密码不正确',403)
                if len(new)<12 or len(new)>128: fail('新密码至少 12 位，最多 128 位')
                c.execute('UPDATE users SET password=? WHERE id=?',(password_hash(new),user['id']))
                c.execute('DELETE FROM sessions WHERE user_id=?',(user['id'],))
                event(c,user['username'],'修改密码'); return self.reply({'ok':True},cookie=self.cookie_header())
            if path=='/api/username' and method=='POST':
                new=required_string(data,'username',32)
                if not USERNAME_RE.fullmatch(new): fail('账号名只能使用字母、数字、点、横线或下划线（3–32 位）')
                if not verify_password(str(data.get('password','')),c.execute('SELECT password FROM users WHERE id=?',(user['id'],)).fetchone()['password']): fail('密码不正确',403)
                c.execute('UPDATE users SET username=? WHERE id=?',(new,user['id']))
                event(c,new,'修改账号'); return self.reply({'ok':True})
            if path=='/api/settings' and method=='POST':
                days=data.get('retention_days')
                if isinstance(days,bool) or not str(days).isdigit() or not 1<=int(days)<=365: fail('保留天数需为 1–365')
                c.execute("UPDATE settings SET value=? WHERE key='retention_days'",(str(days),))
                cleanup(c); event(c,user['username'],'设置日志保留',f'{days} 天'); return self.reply({'ok':True})
            if path=='/api/probe' and method=='POST':
                host=valid_host(required_string(data,'host',253)); p=port(data,'port')
                start=time.monotonic()
                try:
                    with socket.create_connection((host,p),timeout=3): pass
                    return self.reply({'reachable':True,'ms':round((time.monotonic()-start)*1000)})
                except OSError: return self.reply({'reachable':False,'ms':None})
            if path=='/api/events' and method=='GET':
                return self.reply({'events':[dict(e) for e in c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 100')]})
        fail('未找到',404)
    def handle_request(self):
        try: self.dispatch()
        except APIError as e: self.reply({'error':str(e)},e.status)
        except (sqlite3.IntegrityError,ValueError,TypeError) as e: self.reply({'error':'输入冲突或无效'},409)
        except (BrokenPipeError,ConnectionResetError): pass
        except Exception as e:
            sys.stderr.write(f'Internal error: {e!r}\n')
            try: self.reply({'error':'服务器内部错误'},500)
            except OSError: pass
    do_GET=do_POST=do_PUT=do_DELETE=handle_request

class Server(http.server.ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,*args):
        super().__init__(*args); self.login_attempts={}; self.dummy_hash=password_hash('dummy-password')

def main():
    ap=argparse.ArgumentParser(); sub=ap.add_subparsers(dest='command',required=True)
    serve=sub.add_parser('serve'); serve.add_argument('--host',default='127.0.0.1'); serve.add_argument('--port',type=int,default=8765)
    boot=sub.add_parser('bootstrap'); boot.add_argument('--username'); boot.add_argument('--password')
    reset=sub.add_parser('reset-password'); reset.add_argument('--username'); reset.add_argument('--password')
    sub.add_parser('vacuum')
    args=ap.parse_args(); init_db()
    if args.command=='serve':
        if not connect().execute('SELECT 1 FROM users').fetchone(): sys.exit('请先运行 bootstrap 创建管理员')
        server=Server((args.host,args.port),Handler)
        print(f'Nexus-panel listening on {args.host}:{args.port}',flush=True)
        server.serve_forever()
    elif args.command in ('bootstrap','reset-password'):
        username=args.username or os.getenv('NEXUS_ADMIN_USER','admin')
        password=args.password or os.getenv('NEXUS_ADMIN_PASSWORD','')
        if not USERNAME_RE.fullmatch(username) or len(password)<12: sys.exit('账号名至少 3 位；密码至少 12 位')
        with LOCK,connect() as c:
            if args.command=='bootstrap':
                if c.execute('SELECT 1 FROM users').fetchone(): sys.exit('管理员已存在；请使用 reset-password')
                c.execute('INSERT INTO users(username,password) VALUES(?,?)',(username,password_hash(password)))
            else:
                row=c.execute('SELECT id FROM users WHERE username=?',(username,)).fetchone()
                if not row: sys.exit('账号不存在')
                c.execute('UPDATE users SET password=? WHERE id=?',(password_hash(password),row['id']))
                c.execute('DELETE FROM sessions WHERE user_id=?',(row['id'],))
        print('账号已设置')
    else:
        with LOCK,connect() as c: cleanup(c)
        with LOCK,connect() as c: c.execute('VACUUM')
if __name__=='__main__': main()
