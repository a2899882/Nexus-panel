#!/usr/bin/env python3
"""Nexus-panel node agent. Pull-only, authenticated, one supervised Realm process."""
import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

VERSION='0.1.0'

def realm_config(rules):
    endpoints=[]
    for r in rules:
        protocol=r['protocol']
        if protocol not in ('tcp','udp','both'): raise ValueError('invalid protocol')
        endpoints.append({'listen':r['listen'],'remote':r['remote'],
                          'network':{'no_tcp':protocol=='udp','use_udp':protocol!='tcp'}})
    return {'log':{'level':'warn','output':'stdout'},'endpoints':endpoints}

class Agent:
    def __init__(self,url,token,realm,state_dir,interval=5):
        if not url.startswith('https://') and not url.startswith('http://127.0.0.1:'):
            raise ValueError('panel URL must use HTTPS (except local test)')
        self.url=url.rstrip('/')+'/api/agent/poll'; self.token=token
        self.realm=realm; self.dir=Path(state_dir); self.dir.mkdir(parents=True,exist_ok=True)
        self.config=self.dir/'realm.json'; self.proc=None; self.current_hash=''; self.applied_revision=0
        self.rejected_hash=''; self.error=''; self.interval=interval; self.running=True
    def start_realm(self):
        if not self.config.exists(): return
        data=json.loads(self.config.read_text())
        if not data.get('endpoints'): return
        self.proc=subprocess.Popen([self.realm,'-c',str(self.config)],stdout=subprocess.DEVNULL,stderr=sys.stderr)
        time.sleep(1)
        if self.proc.poll() is not None:
            self.proc=None
            raise RuntimeError('Realm 启动失败；请查看 journalctl -u nexus-agent')
    def stop_realm(self):
        if not self.proc: return
        self.proc.terminate()
        try: self.proc.wait(timeout=3)
        except subprocess.TimeoutExpired: self.proc.kill(); self.proc.wait()
        self.proc=None
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
        self.current_hash=hash_value; self.applied_revision=rev; self.error=''; self.rejected_hash=''
    def poll(self):
        if self.proc and self.proc.poll() is not None:
            self.proc=None; self.error='Realm 进程退出，正在重启'
            try: self.start_realm(); self.error=''
            except Exception as e: self.error=str(e)[:240]
        body=json.dumps({'version':VERSION,'applied_revision':self.applied_revision,
                         'realm_running':self.proc is not None and self.proc.poll() is None,
                         'error':self.error}).encode()
        req=urllib.request.Request(self.url,body,{'Authorization':'Bearer '+self.token,
             'Content-Type':'application/json'},method='POST')
        with urllib.request.urlopen(req,timeout=8) as res: desired=json.load(res)
        payload=realm_config(desired['rules'])
        raw=json.dumps(payload,sort_keys=True).encode(); hash_value=hashlib.sha256(raw).hexdigest()
        if hash_value==self.current_hash:
            self.applied_revision=desired['revision']; return
        if hash_value==self.rejected_hash: return
        try: self.apply(payload,desired['revision'],hash_value)
        except Exception as e:
            self.error=str(e)[:240]; self.rejected_hash=hash_value
    def run(self):
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

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--url',default=os.getenv('NEXUS_PANEL_URL',''))
    ap.add_argument('--token',default=os.getenv('NEXUS_AGENT_TOKEN',''))
    ap.add_argument('--realm',default='/usr/local/bin/realm'); ap.add_argument('--state-dir',default='/var/lib/nexus-agent')
    args=ap.parse_args()
    if not args.url or not args.token: ap.error('panel URL and token required')
    Agent(args.url,args.token,args.realm,args.state_dir).run()
if __name__=='__main__': main()
