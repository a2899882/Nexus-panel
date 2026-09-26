import http.cookiejar
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import agent
import panel

class PanelTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        panel.DB_PATH=Path(self.temp.name)/'panel.db'; panel.init_db()
        with panel.connect() as c:
            c.execute('INSERT INTO users(username,password) VALUES(?,?)',('admin',panel.password_hash('correct-horse-battery')))
        self.server=panel.Server(('127.0.0.1',0),panel.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.jar=http.cookiejar.CookieJar();self.opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
    def call(self,path,method='GET',data=None,headers=None,expect=200):
        headers={'X-Nexus-Request':'panel',**(headers or {})}
        raw=json.dumps(data).encode() if data is not None else None
        if raw is not None: headers['Content-Type']='application/json'
        req=urllib.request.Request(self.base+'/api'+path,raw,headers,method=method)
        try: response=self.opener.open(req)
        except urllib.error.HTTPError as e: response=e
        with response: body=json.load(response)
        self.assertEqual(response.status,expect,body)
        return body
    def login(self):
        self.call('/login','POST',{'username':'admin','password':'correct-horse-battery'})
    def enroll(self,created):
        result=self.call('/agent/enroll','POST',{'ticket':created['ticket']})
        self.call('/agent/enroll','POST',{'ticket':created['ticket']},expect=401)
        return result['token']
    def test_auth_rules_agent_and_delete(self):
        self.call('/dashboard',expect=401)
        self.login()
        created=self.call('/nodes','POST',{'name':'Tokyo','address':'example.org','grp':'Asia'},expect=201)
        token=self.enroll(created);self.assertGreater(len(token),40)
        node=created['id']
        rule={'node_id':node,'name':'SSH relay','grp':'work','listen_host':'0.0.0.0','listen_port':22022,
              'remote_host':'127.0.0.1','remote_port':22,'protocol':'tcp','enabled':True}
        made=self.call('/rules','POST',rule,expect=201)
        self.call('/rules','POST',{**rule,'name':'duplicate','protocol':'both'},expect=409)
        self.call('/rules/'+str(made['id']),'PUT',{**rule,'enabled':False})
        poll=self.call('/agent/poll','POST',{'version':'test','applied_revision':0},headers={'Authorization':'Bearer '+token})
        self.assertEqual(poll['rules'],[])
        self.call('/rules/'+str(made['id']),'PUT',rule)
        poll=self.call('/agent/poll','POST',{'version':'test','applied_revision':0},headers={'Authorization':'Bearer '+token})
        self.assertEqual(poll['rules'][0]['listen'],'0.0.0.0:22022')
        self.assertFalse(agent.realm_config(poll['rules'])['endpoints'][0]['network']['use_udp'])
        self.call('/rules/'+str(made['id']),'DELETE')
        self.assertEqual(self.call('/dashboard')['rules'],[])
        self.call('/nodes/'+str(node),'DELETE',expect=409)
        rev=self.call('/dashboard')['revision']
        self.call('/agent/poll','POST',{'version':'test','applied_revision':rev},headers={'Authorization':'Bearer '+token})
        self.call('/nodes/'+str(node),'DELETE')
        self.assertEqual(self.call('/dashboard')['nodes'],[])
    def test_password_csrf_and_rotation(self):
        self.login()
        self.call('/nodes','POST',{'name':'bad'},headers={'X-Nexus-Request':'missing'},expect=403)
        created=self.call('/nodes','POST',{'name':'first'},expect=201)
        token=self.enroll(created)
        node=created['id']
        self.call('/agent/poll','POST',{},headers={'Authorization':'Bearer invalid'},expect=401)
        new=self.call('/nodes/'+str(node)+'/token','POST',{})['token']
        self.call('/agent/poll','POST',{},headers={'Authorization':'Bearer '+token},expect=401)
        self.call('/agent/poll','POST',{},headers={'Authorization':'Bearer '+new})
        self.call('/password','POST',{'current':'wrong','password':'this-is-a-new-password'},expect=403)
        self.call('/password','POST',{'current':'correct-horse-battery','password':'this-is-a-new-password'})
        self.call('/dashboard',expect=401)
        self.call('/login','POST',{'username':'admin','password':'this-is-a-new-password'})
        self.assertEqual(self.call('/dashboard')['nodes'][0]['name'],'first')
    def test_udp_endpoint(self):
        conf=agent.realm_config([{'listen':'0.0.0.0:30001','remote':'example.com:80','protocol':'udp'}])
        self.assertEqual(conf['endpoints'][0]['network'],{'no_tcp':True,'use_udp':True})
    def test_balanced_rule_validation_and_config(self):
        self.login()
        node=self.call('/nodes','POST',{'name':'relay'},expect=201)
        r={'node_id':node['id'],'name':'two exits','listen_host':'127.0.0.1','listen_port':39490,
           'remote_host':'127.0.0.1','remote_port':39491,'protocol':'tcp','enabled':True,
           'extra_remotes':'127.0.0.1:39492','balance':'off'}
        self.call('/rules','POST',r,expect=400)
        self.call('/rules','POST',{**r,'balance':'roundrobin'},expect=201)
        poll=self.call('/agent/poll','POST',{},headers={'Authorization':'Bearer '+self.enroll(node)})
        endpoint=agent.realm_config(poll['rules'])['endpoints'][0]
        self.assertEqual(endpoint['extra_remotes'],['127.0.0.1:39492'])
        self.assertEqual(endpoint['balance'],'roundrobin: 1, 1')
    def test_tunnel_control_plane_and_idempotent_usage(self):
        self.login()
        nodes=[]
        for name in ('front','relay','exit'):
            node=self.call('/nodes','POST',{'name':name,'address':'127.0.0.1'},expect=201)
            nodes.append((node['id'],self.enroll(node)))
        account=self.call('/accounts','POST',{'name':'customer-a','quota_gb':1,'speed_mbps':20,'billing':'both','ratio':1.5,'price_per_gb':2.5,'enabled':True},expect=201)
        tunnel_data={'name':'route-a','account_id':account['id'],'entry_id':nodes[0][0],
            'relay_id':nodes[1][0],'exit_id':nodes[2][0],'entry_port':20221,'relay_port':20222,
            'exit_port':20223,'target_host':'127.0.0.1','target_port':443,'enabled':True}
        route=self.call('/tunnels','POST',tunnel_data,expect=201)
        poll=lambda index,extra=None:self.call('/agent/poll','POST',{'cert_fp':str(index+1)*64,**(extra or {})},headers={'Authorization':'Bearer '+nodes[index][1]})
        self.assertEqual(poll(0)['tunnels'],[])
        poll(1);poll(2)
        desired=poll(0)['tunnels'][0]
        self.assertEqual(desired['role'],'entry')
        self.assertEqual(desired['next_fp'],'2'*64)
        self.assertEqual(poll(1)['tunnels'][0]['role'],'relay')
        record={'route_id':route['id'],'epoch':'a'*32,'up':100,'down':50,'billed':225}
        poll(0,{'usage':[record]});poll(0,{'usage':[record]})
        account_row=self.call('/dashboard')['accounts'][0]
        self.assertEqual((account_row['up_bytes'],account_row['down_bytes'],account_row['used_bytes']),(100,50,225))
        poll(0,{'usage':[{'route_id':route['id'],'epoch':'b'*32,'up':1024**3,'down':0,'billed':1024**3}]})
        self.assertEqual(self.call('/dashboard')['accounts'][0]['amount_cents'],250)
        self.call('/rules','POST',{'node_id':nodes[0][0],'name':'conflict','listen_port':20221,
            'remote_host':'127.0.0.1','remote_port':80,'protocol':'tcp'},expect=409)
        self.call('/nodes/'+str(nodes[0][0]),'DELETE',expect=409)
        self.call('/accounts/'+str(account['id']),'DELETE',expect=409)
        self.call('/tunnels/'+str(route['id']),'DELETE')
        self.call('/accounts/'+str(account['id']),'DELETE')
    def test_chain_tunnel_multiple_forwards_and_shared_account(self):
        self.login()
        nodes=[]
        for label in ('front','middle','exit'):
            made=self.call('/nodes','POST',{'name':label,'address':'127.0.0.1'},expect=201)
            nodes.append((made['id'],self.enroll(made)))
        aid=self.call('/accounts','POST',{'name':'team','quota_gb':1,'speed_mbps':10,'billing':'both','ratio':1,'enabled':True},expect=201)['id']
        tid=self.call('/tunnels','POST',{'name':'CNIX path','mode':'chain','account_id':aid,'entry_id':nodes[0][0],'relay_id':nodes[1][0],'exit_id':nodes[2][0],'enabled':True},expect=201)['id']
        first=self.call('/forwardings','POST',{'name':'SSH','tunnel_id':tid,'entry_port':'','targets':'example.com:22','strategy':'first','enabled':True},expect=201)
        second=self.call('/forwardings','POST',{'name':'Web','tunnel_id':tid,'entry_port':'','targets':'example.com:443\nbackup.example.com:443','strategy':'roundrobin','enabled':True},expect=201)
        self.assertNotEqual(first['entry_port'],second['entry_port'])
        self.call('/forwardings','POST',{'name':'conflict','tunnel_id':tid,'entry_port':first['entry_port'],'targets':'example.com:80'},expect=409)
        self.call('/tunnels/'+str(tid),'DELETE',expect=409)
        def poll(index,payload=None):
            return self.call('/agent/poll','POST',{'cert_fp':str(index+1)*64,**(payload or {})},headers={'Authorization':'Bearer '+nodes[index][1]})
        for index in range(3): poll(index)
        entry=poll(0)['tunnels'];exit_routes=poll(2)['tunnels']
        self.assertEqual({r['id'] for r in entry},{-first['id'],-second['id']})
        self.assertEqual(len(exit_routes[1]['targets']),2)
        self.assertEqual({r['account_id'] for r in entry},{aid})
        poll(0,{'usage':[{'route_id':-first['id'],'epoch':'c'*32,'up':512,'down':256,'billed':768},
                          {'route_id':-second['id'],'epoch':'d'*32,'up':100,'down':50,'billed':150}]})
        poll(0,{'usage':[{'route_id':-first['id'],'epoch':'c'*32,'up':512,'down':256,'billed':768}]})
        account=self.call('/dashboard')['accounts'][0]
        self.assertEqual(account['used_bytes'],918)
        self.call('/forwardings/'+str(first['id']),'DELETE')
        self.call('/forwardings/'+str(second['id']),'DELETE')
        self.call('/tunnels/'+str(tid),'DELETE')
    def test_auto_account_and_legacy_migration(self):
        self.login()
        ids=[self.call('/nodes','POST',{'name':name,'address':'127.0.0.1'},expect=201)['id'] for name in ('entry','relay','exit')]
        base={'name':'simple','mode':'chain','entry_id':ids[0],'relay_id':ids[1],'exit_id':ids[2],'enabled':True}
        self.call('/tunnels','POST',{**base,'exit_id':ids[0]},expect=400)
        self.assertEqual(self.call('/dashboard')['accounts'],[])
        tid=self.call('/tunnels','POST',base,expect=201)['id']
        dashboard=self.call('/dashboard')
        self.assertEqual(len(dashboard['accounts']),1)
        self.assertEqual(dashboard['tunnels'][0]['account_id'],dashboard['accounts'][0]['id'])
        self.call('/tunnels/'+str(tid),'DELETE')
        legacy={**base,'mode':'legacy','account_id':dashboard['accounts'][0]['id'],'entry_port':35550,'relay_port':35551,'exit_port':35552,'target_host':'example.com','target_port':443}
        old_id=self.call('/tunnels','POST',legacy,expect=201)['id']
        self.call('/tunnels/'+str(old_id),'PUT',{**base,'account_id':legacy['account_id']})
        migrated=self.call('/dashboard')['forwardings'][0]
        self.assertEqual((migrated['entry_port'],migrated['targets']),(35550,['example.com:443']))
        self.call('/tunnels/'+str(old_id),'PUT',legacy,expect=409)
if __name__=='__main__': unittest.main()
