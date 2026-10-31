"""Entry-only receiver checks with isolated storage; no tunnel or AI calls needed."""
import base64
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from server import Workspace
from storage import Store
from remote_entries import settings,save_settings,sharing_info,submit_entry
import ai_service

ROOT=Path(__file__).resolve().parents[1]
CODE='sitebrief-test-entry-access-code'
PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8i8AAAAASUVORK5CYII=')

class RemoteEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='sitebrief-entry-')
        self.workspace=Workspace(self.temp.name)
        config=settings(self.workspace.store);config['accessCode']=CODE;self.workspace.store.put('remoteEntrySettings',config)
        env={**os.environ,'SITEBRIEF_DATA_DIR':self.temp.name,'AI_PROVIDER':'demo','OPENAI_API_KEY':'','GEMINI_API_KEY':'','GOOGLE_API_KEY':''}
        self.process=subprocess.Popen([sys.executable,str(ROOT/'webhook_server.py'),'--port','0'],cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        line=self.process.stdout.readline();self.assertIn('Task receiver listening',line)
        self.url='http://127.0.0.1:'+line.split('127.0.0.1:')[1].split(';')[0]
        self.process.stdout.readline()
        self.payload={'projectId':'workspace','type':'note','contributor':'Remote officer','location':'Block A - Level 2','date':self.workspace.state()['today'],'time':'10:30','discipline':'Structural','text':'Remote source note, with no assigned owner or deadline.'}
    def tearDown(self):
        self.process.terminate();self.process.wait(timeout=5);self.process.stdout.close();self.process.stderr.close();self.temp.cleanup()
    def request(self,path,method='GET',payload=None,code=CODE,key='test-request-0001',raw=False,extra_headers=None):
        headers={'Authorization':'Bearer '+code,'Content-Type':'application/json','Idempotency-Key':key,**(extra_headers or {})}
        body=None if payload is None else json.dumps(payload).encode()
        request=urllib.request.Request(self.url+path,data=body,headers=headers,method=method)
        try:
            with urllib.request.urlopen(request,timeout=10) as response:
                data=response.read();return response.status,data if raw else json.loads(data)
        except urllib.error.HTTPError as error:return error.code,json.load(error)
    def test_public_page_is_entry_only_and_context_requires_code(self):
        status,html=self.request('/submit',raw=True,code='')
        self.assertEqual(status,200);self.assertIn(b'Send a site update',html);self.assertNotIn(CODE.encode(),html)
        self.assertEqual(self.request('/entry/context',code='wrong')[0],401)
        status,context=self.request('/entry/context');self.assertEqual(status,200)
        self.assertEqual(context['project']['id'],'workspace');self.assertNotIn('accessCode',context)
        for path in ['/api/state','/api/entry-sharing','/api/models','/files/private','/.env']:
            self.assertEqual(self.request(path)[0],404)
        self.assertNotIn(CODE,json.dumps(self.workspace.state()))
    def test_note_submission_persists_and_retries_do_not_duplicate(self):
        status,result=self.request('/webhooks/updates','POST',self.payload)
        self.assertEqual(status,201);self.assertTrue(result['reference'].startswith('SB-'))
        saved=Store(self.temp.name).get('updates',result['id'])
        self.assertEqual(saved['original']['text'],self.payload['text']);self.assertEqual(saved['submissionOrigin'],'Remote entry')
        status,retry=self.request('/webhooks/updates','POST',self.payload)
        self.assertEqual(status,200);self.assertEqual(retry['id'],result['id']);self.assertTrue(retry['duplicate'])
        self.assertEqual(len(Workspace(self.temp.name).state()['updates']),1)
        self.assertEqual(self.request('/webhooks/updates','POST',{**self.payload,'text':'Changed content'})[0],409)
        log=self.process.stdout.readline();self.assertNotIn(self.payload['text'],log);self.assertNotIn(CODE,log)
    def test_upload_keeps_original_without_automatic_media_processing(self):
        payload={**self.payload,'type':'photo','text':'','caption':'Remote manual caption.','files':[{'name':'site.png','mime':'image/png','data':base64.b64encode(PNG).decode()}]}
        status,result=self.request('/webhooks/updates','POST',payload)
        self.assertEqual(status,201)
        saved=Store(self.temp.name).get('updates',result['id'])
        self.assertEqual(saved['caption'],payload['caption']);self.assertEqual(saved['original']['caption'],payload['caption'])
        self.assertEqual(saved['processing']['image'],'manual')
        self.assertEqual((Path(self.temp.name)/'uploads'/saved['files'][0]['storedName']).read_bytes(),PNG)
        self.assertEqual(self.request(saved['files'][0]['url'])[0],404)
        with patch.object(ai_service,'analyze_media') as analyze:
            config=settings(self.workspace.store)
            submit_entry(self.workspace,payload,config,'direct-request-0002')
            analyze.assert_not_called()
    def test_validation_pausing_and_code_rotation_are_enforced(self):
        for bad in [{**self.payload,'review':'accepted'},{**self.payload,'contributor':''},{**self.payload,'location':3},{**self.payload,'files':'invalid'}]:
            self.assertEqual(self.request('/webhooks/updates','POST',bad)[0],400)
        self.assertEqual(self.request('/webhooks/updates','POST',{**self.payload,'projectId':'other'})[0],403)
        self.assertEqual(self.request('/webhooks/updates','POST',self.payload,key='')[0],400)
        save_settings(self.workspace,{'enabled':False})
        self.assertEqual(self.request('/webhooks/updates','POST',self.payload)[0],503)
        save_settings(self.workspace,{'enabled':True,'rotateCode':True})
        self.assertEqual(self.request('/entry/context')[0],401)
        self.assertEqual(len(self.workspace.store.all('updates')),0)
    def test_project_scoping_and_existing_task_endpoint_remain_separate(self):
        self.workspace.store.put('projects',{'id':'private-project','name':'Selected project'})
        save_settings(self.workspace,{'projectId':'private-project'})
        new_code=settings(self.workspace.store)['accessCode']
        self.assertNotEqual(new_code,CODE)
        self.assertEqual(self.request('/entry/context')[0],401)
        status,context=self.request('/entry/context',code=new_code);self.assertEqual(status,200);self.assertEqual(context['project']['id'],'private-project')
        self.assertNotIn('workspace',json.dumps(context))
        self.assertEqual(self.request('/webhooks/updates','POST',self.payload,code=new_code)[0],403)
        self.assertEqual(self.request('/webhooks/tasks','POST',{'title':'Legacy task','owner':'Sam'},code='')[0],201)
        self.assertEqual(len(self.workspace.store.all('actions')),1)
        self.assertEqual(len(self.workspace.store.all('updates')),0)
    def test_receiver_size_guard_and_sharing_metadata(self):
        self.assertEqual(self.request('/webhooks/updates','POST',self.payload,extra_headers={'Content-Length':str(48*1024*1024+1)})[0],413)
        with patch('remote_entries._local_json',side_effect=[{'app':'SiteBrief entry receiver','entries':True},{'tunnels':[{'public_url':'https://example.ngrok-free.app','config':{'addr':'http://localhost:'+str(settings(self.workspace.store)['receiverPort'])}}]}]):
            info=sharing_info(self.workspace)
        self.assertTrue(info['receiverReady']);self.assertTrue(info['tunnelDetected']);self.assertIn('/submit#access=',info['shareUrl'])
        self.assertNotIn(CODE,info['publicBaseUrl'])
        for url in ['javascript:alert(1)','https://user:password@example.com','https://example.com/submit','https://example.com?code=secret']:
            with self.assertRaises(ValueError):save_settings(self.workspace,{'publicBaseUrl':url})

if __name__=='__main__':unittest.main()
