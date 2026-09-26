#!/usr/bin/env python3
"""Small authenticated TLS TCP chain used by Nexus-panel tunnel accounts."""
import argparse
import hashlib
import hmac
import itertools
import json
import os
import socket
import sqlite3
import ssl
import threading
import time
import uuid
from pathlib import Path

MAX_HELLO=512
ROUND_ROBIN={}
ROUND_LOCK=threading.Lock()

def pinned_connection(host,port,fingerprint):
    raw=socket.create_connection((host,port),timeout=8)
    conn=None
    try:
        ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname=False
        ctx.verify_mode=ssl.CERT_NONE
        conn=ctx.wrap_socket(raw,server_hostname=None)
        if not hmac.compare_digest(hashlib.sha256(conn.getpeercert(binary_form=True)).hexdigest(),fingerprint):
            raise OSError('downstream certificate fingerprint mismatch')
        conn.settimeout(None)
        return conn
    except Exception:
        if conn: conn.close()
        raw.close(); raise

def recv_line(conn):
    data=bytearray()
    while len(data)<MAX_HELLO:
        b=conn.recv(1)
        if not b: raise OSError('connection closed before handshake')
        if b==b'\n': return json.loads(data)
        data.extend(b)
    raise OSError('handshake too large')

def dial(route):
    if route['role']=='exit':
        targets=route.get('targets') or [{'host':route['target_host'],'port':route['target_port']}]
        with ROUND_LOCK:
            index=next(ROUND_ROBIN.setdefault(route['id'],itertools.count())) if route.get('strategy')=='roundrobin' else 0
        target=targets[index%len(targets)]
        return socket.create_connection((target['host'],target['port']),timeout=8)
    conn=pinned_connection(route['next_host'],route['next_port'],route['next_fp'])
    try:
        conn.sendall(json.dumps({'id':route['id'],'secret':route['secret']}).encode()+b'\n')
        conn.settimeout(10)
        if conn.recv(1)!=b'\x01': raise OSError('downstream rejected route')
        conn.settimeout(None)
        return conn
    except Exception: conn.close(); raise

class Meter:
    def __init__(self,path,routes):
        self.path=Path(path); self.lock=threading.RLock(); self.routes={int(r['id']):r for r in routes if r['role']=='entry'}
        self.db=sqlite3.connect(self.path,check_same_thread=False,timeout=10)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS usage (route_id INTEGER PRIMARY KEY, epoch TEXT NOT NULL, up INTEGER NOT NULL, down INTEGER NOT NULL, billed INTEGER NOT NULL)')
        self.state={}; self.groups={}
        for rid,route in self.routes.items():
            row=self.db.execute('SELECT epoch,up,down,billed FROM usage WHERE route_id=?',(rid,)).fetchone()
            if row is None:
                row=(uuid.uuid4().hex,0,0,0)
                with self.db: self.db.execute('INSERT INTO usage VALUES(?,?,?,?,?)',(rid,*row))
            aid=int(route.get('account_id',rid))
            self.state[rid]={'epoch':row[0],'up':row[1],'down':row[2],'billed':row[3],
                             'account_id':aid}
            group=self.groups.setdefault(aid,{'billed':0,'baseline':0,'tokens':0.0,'last':time.monotonic()})
            group['billed']+=row[3]
        for rid,route in self.routes.items():
            group=self.groups[self.state[rid]['account_id']]
            group['baseline']=max(group['baseline'],int(route['used_bytes'])-group['billed'],0)
    def charge(self,rid,direction,count):
        if not count: return 0
        route=self.routes[rid]
        with self.lock:
            state=self.state[rid]
            group=self.groups[state['account_id']]
            speed=int(route['speed_bps'])
            if speed:
                while True:
                    instant=time.monotonic()
                    group['tokens']=min(max(speed,16384),group['tokens']+(instant-group['last'])*speed)
                    group['last']=instant
                    if group['tokens']>=1: break
                    time.sleep(min(.05,1/speed))
                count=min(count,max(1,int(group['tokens'])))
            factor=int(route['ratio_bp']) if route['billing'] in (direction,'both') else 0
            quota=int(route['quota_bytes'])
            if quota and factor:
                remain=quota-group['baseline']-group['billed']
                if remain<=0: return 0
                count=min(count,max(1,(remain*10000)//factor))
                # Recheck after rounding, including fractional bytes.
                while count>0 and ((state[direction]+count)*factor//10000-state[direction]*factor//10000)>remain: count-=1
                if not count: return 0
            if speed: group['tokens']-=count
            old=state[direction]; state[direction]+=count
            # Billing uses cumulative directional totals, avoiding per-chunk rounding loss.
            increment=((state[direction]*factor)//10000)-((old*factor)//10000)
            state['billed']+=increment
            group['billed']+=increment
            with self.db:
                self.db.execute('UPDATE usage SET up=?,down=?,billed=? WHERE route_id=?',
                                (state['up'],state['down'],state['billed'],rid))
            return count
    def snapshot(self):
        with self.lock:
            return [{'route_id':rid,'epoch':s['epoch'],'up':s['up'],'down':s['down'],'billed':s['billed']} for rid,s in self.state.items()]

def forward(left,right,route,meter):
    rid=int(route['id']); done=threading.Event()
    def exact(src,length):
        data=bytearray()
        while len(data)<length:
            chunk=src.recv(length-len(data))
            if not chunk: raise OSError('truncated tunnel frame')
            data.extend(chunk)
        return bytes(data)
    def pump(src,dst,direction,framed_src,framed_dst):
        try:
            while not done.is_set():
                if framed_src:
                    length=int.from_bytes(exact(src,2),'big')
                    if length>16384: raise OSError('invalid tunnel frame')
                    chunk=exact(src,length) if length else b''
                else: chunk=src.recv(16384)
                if not chunk: break
                while chunk:
                    allowed=meter.charge(rid,direction,len(chunk)) if meter else len(chunk)
                    if not allowed: raise OSError('traffic quota exhausted')
                    part=chunk[:allowed]
                    dst.sendall(len(part).to_bytes(2,'big')+part if framed_dst else part)
                    chunk=chunk[allowed:]
            if framed_dst: dst.sendall(b'\x00\x00')
            else: dst.shutdown(socket.SHUT_WR)
        except (OSError,ssl.SSLError):
            done.set()
            for sock in (left,right):
                try: sock.close()
                except OSError: pass
    worker=threading.Thread(target=pump,args=(left,right,'up',route['role']!='entry',route['role']!='exit'),daemon=True);worker.start()
    pump(right,left,'down',route['role']!='exit',route['role']!='entry');worker.join(timeout=15)

def handle(conn,route,meter):
    downstream=None
    try:
        conn.settimeout(15)
        if route['role']!='entry':
            hello=recv_line(conn)
            if hello.get('id')!=route['id'] or not isinstance(hello.get('secret'),str) or not hmac.compare_digest(hello['secret'],route['secret']): return
        downstream=dial(route)
        if route['role']!='entry': conn.sendall(b'\x01')
        conn.settimeout(None); downstream.settimeout(None)
        forward(conn,downstream,route,meter if route['role']=='entry' else None)
    except (OSError,ValueError,ssl.SSLError,json.JSONDecodeError) as exc:
        print(f"route {route['id']}: {exc}",flush=True)
    finally:
        conn.close()
        if downstream: downstream.close()

def serve(route,ctx,meter,listener):
    print(f"route {route['id']} {route['role']} listening on {route['listen']}",flush=True)
    while True:
        raw,_=listener.accept()
        def accepted(sock):
            try:
                sock.settimeout(10)
                conn=ctx.wrap_socket(sock,server_side=True) if route['role']!='entry' else sock
                handle(conn,route,meter)
            except (OSError,ssl.SSLError) as exc:
                print(f"route {route['id']} handshake: {exc}",flush=True)
                sock.close()
        threading.Thread(target=accepted,args=(raw,),daemon=True).start()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--config',required=True);ap.add_argument('--cert',required=True);ap.add_argument('--key',required=True);ap.add_argument('--usage',required=True)
    args=ap.parse_args();routes=json.loads(Path(args.config).read_text())
    if not routes: return
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);ctx.load_cert_chain(args.cert,args.key)
    meter=Meter(args.usage,routes)
    listeners=[]
    try:
        for route in routes:
            host,port=route['listen'].rsplit(':',1)
            listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            listener.bind((host,int(port)));listener.listen(256)
            listeners.append(listener)
    except Exception:
        for listener in listeners: listener.close()
        raise
    threads=[threading.Thread(target=serve,args=(route,ctx,meter,listener),daemon=False) for route,listener in zip(routes,listeners)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
if __name__=='__main__': main()
