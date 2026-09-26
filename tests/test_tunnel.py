import hashlib
import json
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import tunnel

ROOT=Path(__file__).resolve().parents[1]

def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));return s.getsockname()[1]

class TunnelTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name);self.processes=[]
        self.addCleanup(self.stop)
    def stop(self):
        for proc in self.processes:
            proc.terminate()
            try: proc.wait(timeout=3)
            except subprocess.TimeoutExpired: proc.kill();proc.wait()
            proc.stdout.close()
    def cert(self,name):
        key=self.path/(name+'.key');cert=self.path/(name+'.crt')
        subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
            '-subj','/CN='+name,'-keyout',str(key),'-out',str(cert)],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
        der=subprocess.check_output(['openssl','x509','-in',str(cert),'-outform','DER'])
        return cert,key,hashlib.sha256(der).hexdigest()
    def launch(self,name,route,cert,key):
        routes=route if isinstance(route,list) else [route]
        config=self.path/(name+'.json');config.write_text(json.dumps(routes))
        proc=subprocess.Popen([sys.executable,str(ROOT/'tunnel.py'),'--config',str(config),
            '--cert',str(cert),'--key',str(key),'--usage',str(self.path/(name+'.db'))],
            stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        self.processes.append(proc)
        for route in routes:
            port=int(route['listen'].rsplit(':',1)[1])
            for _ in range(100):
                try:
                    with socket.create_connection(('127.0.0.1',port),timeout=.1): break
                except OSError: time.sleep(.02)
            else: self.fail(f'{name} did not listen on {port}, exit code {proc.poll()}')
    def test_two_real_three_hop_forwards_share_account(self):
        targets=[]
        for prefix in (b'A',b'B'):
            server=socket.socket();server.bind(('127.0.0.1',0));server.listen();self.addCleanup(server.close)
            def echo(listener,marker):
                while True:
                    try: client,_=listener.accept()
                    except OSError: return
                    with client:
                        while data:=client.recv(4096): client.sendall(marker+data)
            threading.Thread(target=echo,args=(server,prefix),daemon=True).start()
            targets.append(server.getsockname()[1])
        ports=[[free_port() for _ in range(3)] for _ in range(2)]
        certs=[self.cert(x) for x in ('entry','relay','exit')]
        entry=[];relay=[];exit_routes=[]
        for i in range(2):
            rid=-(i+1);base={'id':rid,'secret':('a' if i==0 else 'b')*64}
            entry.append(dict(base,role='entry',listen=f'127.0.0.1:{ports[i][0]}',next_host='127.0.0.1',next_port=ports[i][1],next_fp=certs[1][2],account_id=8,quota_bytes=100,speed_bps=0,billing='both',ratio_bp=10000,used_bytes=0))
            relay.append(dict(base,role='relay',listen=f'127.0.0.1:{ports[i][1]}',next_host='127.0.0.1',next_port=ports[i][2],next_fp=certs[2][2]))
            exit_routes.append(dict(base,role='exit',listen=f'127.0.0.1:{ports[i][2]}',targets=[{'host':'127.0.0.1','port':targets[i]}],strategy='first'))
        self.launch('exit',exit_routes,*certs[2][:2])
        self.launch('relay',relay,*certs[1][:2])
        self.launch('entry',entry,*certs[0][:2])
        for i,prefix in enumerate((b'A',b'B')):
            with socket.create_connection(('127.0.0.1',ports[i][0]),timeout=4) as conn:
                conn.settimeout(4);conn.sendall(b'ping');self.assertEqual(conn.recv(5),prefix+b'ping')
        with sqlite3_connect(self.path/'entry.db') as db:
            for _ in range(50):
                rows=db.execute('SELECT route_id,up,down,billed FROM usage ORDER BY route_id').fetchall()
                if len(rows)==2 and all(row[3]>=9 for row in rows): break
                time.sleep(.02)
        self.assertEqual(rows,[(-2,4,5,9),(-1,4,5,9)])
    def test_real_three_hop_tls_tcp_and_accounting(self):
        echo=socket.socket();echo.bind(('127.0.0.1',0));echo.listen();self.addCleanup(echo.close)
        def server():
            while True:
                try: client,_=echo.accept()
                except OSError: return
                def reply(conn):
                    with conn:
                        while data:=conn.recv(4096): conn.sendall(data)
                threading.Thread(target=reply,args=(client,),daemon=True).start()
        threading.Thread(target=server,daemon=True).start()
        ports=[free_port() for _ in range(3)]
        certs=[self.cert(name) for name in ('entry','relay','exit')]
        secret='b'*64
        base={'id':7,'secret':secret}
        self.launch('exit',dict(base,role='exit',listen=f'127.0.0.1:{ports[2]}',target_host='127.0.0.1',target_port=echo.getsockname()[1]),*certs[2][:2])
        self.launch('relay',dict(base,role='relay',listen=f'127.0.0.1:{ports[1]}',next_host='127.0.0.1',next_port=ports[2],next_fp=certs[2][2]),*certs[1][:2])
        self.launch('entry',dict(base,role='entry',listen=f'127.0.0.1:{ports[0]}',next_host='127.0.0.1',next_port=ports[1],next_fp=certs[1][2],quota_bytes=50,speed_bps=0,billing='both',ratio_bp=10000,used_bytes=0),*certs[0][:2])
        with socket.create_connection(('127.0.0.1',ports[0]),timeout=4) as conn:
            conn.settimeout(4);conn.sendall(b'hello');self.assertEqual(conn.recv(5),b'hello')
        with socket.create_connection(('127.0.0.1',ports[0]),timeout=4) as conn:
            conn.settimeout(4);conn.sendall(b'world');conn.shutdown(socket.SHUT_WR)
            self.assertEqual(conn.recv(5),b'world')
            self.assertEqual(conn.recv(1),b'')
        with sqlite3_connect(self.path/'entry.db') as db:
            for _ in range(50):
                row=db.execute('SELECT up,down,billed FROM usage WHERE route_id=7').fetchone()
                if row and row[2]>=20: break
                time.sleep(.02)
        self.assertEqual(row,(10,10,20))
        with socket.create_connection(('127.0.0.1',ports[1]),timeout=4) as raw:
            ctx=ssl._create_unverified_context()
            with ctx.wrap_socket(raw) as conn:
                conn.settimeout(3)
                conn.sendall(json.dumps({'id':7,'secret':'wrong'}).encode()+b'\n')
                self.assertEqual(conn.recv(1),b'')
        with self.assertRaises(OSError): tunnel.pinned_connection('127.0.0.1',ports[1],'0'*64)
    def test_quota_and_persistent_usage(self):
        config=dict(id=3,role='entry',quota_bytes=5,speed_bps=0,billing='both',ratio_bp=10000,used_bytes=0)
        meter=tunnel.Meter(self.path/'usage.db',[config])
        self.assertEqual(meter.charge(3,'up',9),5)
        self.assertEqual(meter.charge(3,'down',1),0)
        snapshot=meter.snapshot();self.assertEqual(snapshot[0]['billed'],5)
        meter.db.close()
        reopened=tunnel.Meter(self.path/'usage.db',[dict(config,used_bytes=5)])
        self.assertEqual(reopened.snapshot(),snapshot)
        self.assertEqual(reopened.charge(3,'up',1),0)
        reopened.db.close()
    def test_shared_quota_for_two_forwards(self):
        base=dict(role='entry',account_id=9,quota_bytes=10,speed_bps=0,billing='both',ratio_bp=10000,used_bytes=0)
        meter=tunnel.Meter(self.path/'shared.db',[dict(base,id=-1),dict(base,id=-2)])
        self.assertEqual(meter.charge(-1,'up',7),7)
        self.assertEqual(meter.charge(-2,'up',7),3)
        self.assertEqual(meter.charge(-1,'up',1),0)
        meter.db.close()
        reopened=tunnel.Meter(self.path/'shared.db',[dict(base,id=-1,used_bytes=10),dict(base,id=-2,used_bytes=10)])
        self.assertEqual(reopened.charge(-2,'down',1),0)
        reopened.db.close()

def sqlite3_connect(path):
    import sqlite3
    return sqlite3.connect(path)

if __name__=='__main__':unittest.main()
