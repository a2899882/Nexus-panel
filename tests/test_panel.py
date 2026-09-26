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
if __name__=='__main__': unittest.main()
