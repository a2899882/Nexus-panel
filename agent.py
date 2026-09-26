#!/usr/bin/env python3
"""Nexus-panel node agent. Pull-only, authenticated, one supervised Realm process."""
import argparse
import hashlib
import json
import os
import collections
import signal
import subprocess
import sys
import threading
import time
import sqlite3
import urllib.error
import urllib.request
from pathlib import Path

VERSION='0.2.0'

def realm_config(rules):
    endpoints=[]
    for r in rules:
        protocol=r['protocol']
        if protocol not in ('tcp','udp','both'): raise ValueError('invalid protocol')
        endpoint={'listen':r['listen'],'remote':r['remote'],
                  'network':{'no_tcp':protocol=='udp','use_udp':protocol!='tcp'}}
        extras=r.get('extra_remotes') or []
        if extras:
            endpoint['extra_remotes']=extras
            endpoint['balance']=r['balance']+': '+', '.join(['1']*(len(extras)+1))
        endpoints.append(endpoint)
    return {'log':{'level':'warn','output':'stdout'},'endpoints':endpoints}

class Agent:
    def __init__(self,url,token,realm,state_dir,interval=5):
        if not url.startswith('https://') and not url.startswith('http://127.0.0.1:'):
            raise ValueError('panel URL must use HTTPS (except local test)')
        self.url=url.rstrip('/')+'/api/agent/poll'; self.token=token
        self.realm=realm; self.dir=Path(state_dir); self.dir.mkdir(parents=True,exist_ok=True)
        self.config=self.dir/'realm.json'; self.proc=None; self.current_hash=''; self.applied_revision=0
        self.rejected_hash=''; self.last_reject_at=0; self.error=''; self.interval=interval; self.running=True
        self.log_tail=collections.deque(maxlen=25)
        self.tunnel_config=self.dir/'tunnels.json'; self.tunnel_proc=None; self.tunnel_hash=''
        self.cert=self.dir/'tunnel.crt'; self.key=self.dir/'tunnel.key'
        if not self.cert.exists() or not self.key.exists():
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','3650',
                '-subj','/CN=nexus-agent','-keyout',str(self.key),'-out',str(self.cert)],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.key.chmod(0o600)
        der=subprocess.check_output(['openssl','x509','-in',str(self.cert),'-outform','DER'])
        self.cert_fp=hashlib.sha256(der).hexdigest()
    def start_realm(self):
        if not self.config.exists(): return
        data=json.loads(self.config.read_text())
        if not data.get('endpoints'): return
        self.log_tail.clear()
        self.proc=subprocess.Popen([self.realm,'-c',str(self.config)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
        proc=self.proc
        def drain():
            for line in proc.stdout:
                message=line.decode(errors='replace').strip()
                self.log_tail.append(message)
                print('realm:',message,file=sys.stderr,flush=True)
            proc.stdout.close()
        threading.Thread(target=drain,daemon=True).start()
        time.sleep(1)
        if self.proc.poll() is not None:
            self.proc=None
            detail=' '.join(self.log_tail)[-190:]
            raise RuntimeError('Realm 启动失败：'+(detail or '请查看 journalctl -u nexus-agent'))
    def stop_realm(self):
        if not self.proc: return
        self.proc.terminate()
        try: self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait()
        self.proc=None
    def stop_tunnel(self):
        if self.tunnel_proc:
            self.tunnel_proc.terminate()
            try: self.tunnel_proc.wait(timeout=3)
            except subprocess.TimeoutExpired: self.tunnel_proc.kill(); self.tunnel_proc.wait()
            self.tunnel_proc=None
    def start_tunnel(self):
        if not self.tunnel_config.exists() or not json.loads(self.tunnel_config.read_text()): return
        self.tunnel_proc=subprocess.Popen([sys.executable,str(Path(__file__).with_name('tunnel.py')),
            '--config',str(self.tunnel_config),'--cert',str(self.cert),'--key',str(self.key),
            '--usage',str(self.dir/'usage.db')],stdout=sys.stderr,stderr=sys.stderr)
        time.sleep(.5)
        if self.tunnel_proc.poll() is not None:
            self.tunnel_proc=None; raise RuntimeError('隧道启动失败；请查看 journalctl -u nexus-agent')
    def usage(self):
        path=self.dir/'usage.db'
        if not path.exists(): return []
        with sqlite3.connect(path,timeout=5) as db:
            return [{'route_id':row[0],'epoch':row[1],'up':row[2],'down':row[3],'billed':row[4]}
                for row in db.execute('SELECT route_id,epoch,up,down,billed FROM usage')]
    def apply(self,payload,rev,hash_value):
        old=self.config.read_bytes() if self.config.exists() else None
        candidate=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode()
        self.stop_realm()
        temp=self.dir/'realm.json.tmp'; temp.write_bytes(candidate); temp.replace(self.config)
        try: self.start_realm()
        except Exception:
            self.stop_realm()
            if old is not None: self.config.write_bytes(old)
            else: self.config.unlink(missing_ok=True)
            self.start_realm()
            raise
        self.current_hash=hash_value; self.rejected_hash=''
    def poll(self):
        if self.proc and self.proc.poll() is not None:
            self.proc=None; self.error='Realm 进程退出，正在重启'
            try: self.start_realm(); self.error=''
            except Exception as e: self.error=str(e)[:240]
        if self.tunnel_proc and self.tunnel_proc.poll() is not None:
            self.tunnel_proc=None; self.error='隧道进程退出，正在重启'
            try: self.start_tunnel(); self.error=''
            except Exception as e: self.error=str(e)[:240]
        body=json.dumps({'version':VERSION,'applied_revision':self.applied_revision,
                         'realm_running':self.proc is not None and self.proc.poll() is None,
                         'tunnel_running':self.tunnel_proc is not None and self.tunnel_proc.poll() is None,
                         'cert_fp':self.cert_fp,'usage':self.usage(),'error':self.error}).encode()
        req=urllib.request.Request(self.url,body,{'Authorization':'Bearer '+self.token,
             'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=8) as res: desired=json.load(res)
        tunnels=desired.get('tunnels',[])
        topology=[{k:v for k,v in t.items() if k!='used_bytes'} for t in tunnels]
        tunnel_hash=hashlib.sha256(json.dumps(topology,sort_keys=True).encode()).hexdigest()
        tunnel_error=''
        if tunnel_hash!=self.tunnel_hash or tunnels and not self.tunnel_proc:
            self.stop_tunnel()
            temp=self.dir/'tunnels.json.tmp'; temp.write_text(json.dumps(tunnels)); temp.replace(self.tunnel_config)
            try:
                self.start_tunnel(); self.tunnel_hash=tunnel_hash
            except Exception as e: tunnel_error=str(e)[:240]
        payload=realm_config(desired['rules'])
        raw=json.dumps(payload,sort_keys=True).encode(); hash_value=hashlib.sha256(raw).hexdigest()
        realm_ok=hash_value==self.current_hash and (not payload['endpoints'] or self.proc and self.proc.poll() is None)
        if not realm_ok and (hash_value!=self.rejected_hash or time.monotonic()-self.last_reject_at>30):
            try: self.apply(payload,desired['revision'],hash_value); realm_ok=True
            except Exception as e:
                self.error=str(e)[:240]; self.rejected_hash=hash_value; self.last_reject_at=time.monotonic()
        if tunnel_error: self.error=tunnel_error
        elif realm_ok: self.error=''
        if realm_ok and not tunnel_error and (not tunnels or self.tunnel_proc and self.tunnel_proc.poll() is None):
            self.applied_revision=desired['revision']
    def run(self):
        if self.tunnel_config.exists():
            try:
                tunnels=json.loads(self.tunnel_config.read_text())
                topology=[{k:v for k,v in t.items() if k!='used_bytes'} for t in tunnels]
                self.tunnel_hash=hashlib.sha256(json.dumps(topology,sort_keys=True).encode()).hexdigest()
                self.start_tunnel()
            except Exception as e: self.error=str(e)[:240]
        if self.config.exists():
            try:
                payload=json.loads(self.config.read_text())
                self.current_hash=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
                self.start_realm()
            except Exception as e: self.error=str(e)[:240]
        def stop(*_): self.running=False
        signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
        while self.running:
            try: self.poll()
            except (OSError,urllib.error.URLError,ValueError,KeyError) as e:
                print('agent poll:',e,file=sys.stderr,flush=True)
            for _ in range(self.interval*10):
                if not self.running: break
                time.sleep(.1)
        self.stop_realm()
        self.stop_tunnel()

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--url',default=os.getenv('NEXUS_PANEL_URL',''))
    ap.add_argument('--token',default=os.getenv('NEXUS_AGENT_TOKEN',''))
    ap.add_argument('--realm',default='/usr/local/bin/realm'); ap.add_argument('--state-dir',default='/var/lib/nexus-agent')
    args=ap.parse_args()
    if not args.url or not args.token: ap.error('panel URL and token required')
    Agent(args.url,args.token,args.realm,args.state_dir).run()
if __name__=='__main__': main()
