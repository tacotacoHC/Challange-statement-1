import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from storage import Store

ROOT = Path(__file__).resolve().parents[1]


class WebhookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / 'webhook_server.py'), '--port', '0'],
            cwd=ROOT, env=dict(os.environ, SITEBRIEF_DATA_DIR=self.temp.name),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        # Port 0 is selected by the OS; startup output reports the actual port.
        line = self.process.stdout.readline()
        self.assertIn('Task receiver listening', line)
        self.url = 'http://127.0.0.1:' + line.split('127.0.0.1:')[1].split(';')[0]

    def tearDown(self):
        self.process.terminate()
        self.process.wait(timeout=5)
        self.process.stdout.close()
        self.process.stderr.close()
        self.temp.cleanup()

    def request(self, path='/webhooks/tasks', body=b'{}', content_type='application/json', method='POST', token=None):
        headers = {'Content-Type': content_type}
        if token: headers['Authorization'] = 'Bearer ' + token
        req = Request(self.url + path, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=5) as response:
                return response.status, json.load(response)
        except HTTPError as exc:
            return exc.code, json.load(exc)

    def test_create_and_reject_invalid_requests(self):
        payload = {'title': 'Inspect pump', 'owner': 'Sam', 'priority': 'High'}
        status, result = self.request(body=json.dumps(payload).encode())
        self.assertEqual(status, 201)
        action = Store(self.temp.name).get('actions', result['id'])
        self.assertEqual((action['title'], action['owner'], action['projectId'], action['origin']),
                         ('Inspect pump', 'Sam', 'workspace', 'External integration'))
        self.assertEqual(self.request(path='/api/state')[0], 404)
        self.assertEqual(self.request(body=b'{bad')[0], 400)
        self.assertEqual(self.request(body=b'{"title":""}')[0], 400)
        self.assertEqual(self.request(body=b'{"title":"x","review":"accepted"}')[0], 400)
        self.assertEqual(self.request(body=b'{"title":"x","projectId":"missing"}')[0], 404)
        self.assertEqual(self.request(body=b' ' * (64 * 1024 + 1))[0], 413)
        self.assertEqual(len(Store(self.temp.name).all('actions')), 1)


    def test_keyword_status_updates_are_scoped_and_unambiguous(self):
        store = Store(self.temp.name)
        _, first = self.request(body=json.dumps({'title':'Inspect pump P-02', 'description':'Basement leak'}).encode())
        _, second = self.request(body=json.dumps({'title':'Inspect pump P-03'}).encode())
        def patch(path, keywords, status, **extra):
            return self.request(path=path, method='PATCH',
                                body=json.dumps({'keywords':keywords, 'status':status, **extra}).encode())
        self.assertEqual(patch('/tasks', 'pump', 'Resolved')[0], 409)
        self.assertEqual(patch('/tasks', 'missing', 'Resolved')[0], 404)
        self.assertEqual(patch('/tasks', 'P-02', 'finished')[0], 400)
        self.assertEqual(patch('/tasks', 'P-02', 'Open', projectId='another-project')[0], 404)
        store.put('projects', {'id':'another-project', 'name':'Another project'})
        store.put('actions', {'id':'other-action', 'projectId':'another-project', 'title':'Inspect pump P-02', 'status':'Open', 'review':'accepted', 'sourceIds':[]})
        self.assertEqual(patch('/tasks', 'P-02', 'Resolved', projectId='another-project')[0], 200)
        self.assertEqual(store.get('actions', 'other-action')['status'], 'Resolved')
        self.assertEqual(store.get('actions', first['id'])['status'], 'Open')
        for status in ('In Progress', 'Resolved', 'Open'):
            code, result = patch('/webhooks/tasks', ['P-02', 'basement'], status.lower())
            self.assertEqual((code, result['id'], result['status']), (200, first['id'], status))
            saved = store.get('actions', first['id'])
            self.assertEqual(saved['status'], status)
            self.assertEqual('resolvedAt' in saved, status == 'Resolved')
        self.assertEqual(store.get('actions', second['id'])['status'], 'Open')
        store.put('issues', {'id':'issue-test', 'projectId':'workspace', 'title':'Cracked slab',
                            'description':'Near stair A', 'priority':'High', 'status':'Open', 'review':'pending', 'sourceIds':[]})
        code, result = patch('/webhooks/issues', 'cracked stair', 'in progress')
        self.assertEqual((code, result['kind'], result['id']), (200, 'issue', 'issue-test'))
        saved = store.get('issues', 'issue-test')
        self.assertEqual((saved['status'], saved['review']), ('In Progress', 'pending'))


if __name__ == '__main__':
    unittest.main()
