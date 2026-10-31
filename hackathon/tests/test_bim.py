"""BIM annotations must stay durable, evidence-linked and explicitly reviewed."""
import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bim_service import index_ifc, suggest_element, IfcIndexError, MAX_MODEL_BYTES
from server import Workspace, ApiError, Handler, ThreadingHTTPServer

DEMO = (ROOT / 'public' / 'demo' / 'riverside.ifc').read_bytes()
PROJECT = 'project-riverside'


class BIMWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {'AI_PROVIDER': 'demo', 'OPENAI_API_KEY': '', 'GEMINI_API_KEY': '', 'GOOGLE_API_KEY': '', 'SITEBRIEF_QUIET': '1'})
        self.environment.start()
        self.directory = tempfile.TemporaryDirectory(prefix='sitebrief-bim-')
        self.workspace = Workspace(self.directory.name)
        self.demo = self.workspace.load_demo()

    def tearDown(self):
        self.directory.cleanup()
        self.environment.stop()

    def load_model(self):
        state = self.workspace.load_demo_model({'projectId': PROJECT})
        return state, next(model for model in state['models'] if model['projectId'] == PROJECT)

    def report_issue(self, source='demo-pump'):
        state = self.workspace.generate({'projectId': PROJECT, 'date': self.demo['today']})
        return state, next(issue for issue in state['issues'] if source in issue['sourceIds'])

    def test_real_ifc_index_and_original_bytes_survive_restart(self):
        state, model = self.load_model()
        self.assertEqual(model['schema'], 'IFC4')
        self.assertGreaterEqual(model['elementCount'], 49)
        pump = next(e for e in model['elements'] if e['tag'] == 'P-02')
        self.assertEqual(pump['name'], 'Dewatering pump P-02')
        self.assertEqual(pump['storey'], 'Basement')
        self.assertEqual(len(pump['globalId']), 22)
        level3 = next(e for e in model['elements'] if e['tag'] == 'A-L3-SLAB')
        self.assertEqual(level3['storey'], 'Block A - Level 3')
        self.assertEqual(model['sha256'], hashlib.sha256(DEMO).hexdigest())
        stored = self.workspace.store.directory / 'uploads' / model['storedName']
        self.assertEqual(stored.read_bytes(), DEMO)
        restarted = Workspace(self.directory.name).state()
        saved = next(m for m in restarted['models'] if m['id'] == model['id'])
        self.assertEqual(saved['elements'], model['elements'])
        self.assertEqual(saved['sha256'], model['sha256'])
        self.assertTrue(state['activity'][0]['projectId'] == PROJECT)

    def test_repeat_upload_is_idempotent_but_models_remain_project_scoped(self):
        _, first = self.load_model()
        for _ in range(2):
            state = self.workspace.save_model({'projectId': PROJECT, 'name': 'Renamed upload', 'file': {
                'name': 'same.ifc', 'data': base64.b64encode(DEMO).decode()}})
        self.assertEqual(len(state['models']), 1)
        self.assertEqual(state['models'][0]['id'], first['id'])
        state = self.workspace.save_model({'projectId': 'workspace', 'file': {
            'name': 'other-project.ifc', 'data': base64.b64encode(DEMO).decode()}})
        self.assertEqual(len(state['models']), 2)
        self.assertEqual(len({m['id'] for m in state['models']}), 2)
        self.assertEqual(len(list((self.workspace.store.directory / 'uploads').glob('*.ifc'))), 2)

    def test_invalid_and_oversized_models_are_rejected_without_saved_partial_records(self):
        malformed = [b'', b'not IFC', DEMO.replace(b"FILE_SCHEMA(('IFC4'))", b"FILE_SCHEMA(('NOTIFC'))"),
                     DEMO.replace(b'END-ISO-10303-21;', b''),
                     DEMO.replace(b'DATA;\n', b"DATA;\n#1=IFCCARTESIANPOINT((0.,0.,0.));\n", 1)]
        for blob in malformed:
            with self.subTest(size=len(blob)), self.assertRaises((ApiError, IfcIndexError)):
                self.workspace.save_model({'projectId': PROJECT, 'file': {'name': 'invalid.ifc'}}, blob=blob)
        with self.assertRaises(ApiError):
            self.workspace.save_model({'projectId': PROJECT, 'file': {'name': 'image.png'}}, blob=DEMO)
        with self.assertRaises(ApiError):
            self.workspace.save_model({'projectId': PROJECT, 'file': {'name': 'bad.ifc', 'data': '@not-base64'}})
        with self.assertRaises(ApiError) as error:
            self.workspace.save_model({'projectId': PROJECT, 'file': {'name': 'large.ifc'}}, blob=b'x' * (MAX_MODEL_BYTES + 1))
        self.assertEqual(error.exception.status, 413)
        self.assertEqual(self.workspace.state()['models'], [])
        self.assertEqual(list((self.workspace.store.directory / 'uploads').glob('*.ifc')), [])

    def test_suggestions_are_source_bound_and_never_confirm_themselves(self):
        state, model = self.load_model()
        state, pump_issue = self.report_issue()
        issues_before = copy.deepcopy(state['issues'])
        suggestions = self.workspace.model_suggestions(model['id'])
        self.assertTrue(suggestions['suggestions'])
        pump = next(s for s in suggestions['suggestions'] if s.get('issueId') == pump_issue['id'])
        self.assertEqual(pump['expressId'], 683)
        self.assertEqual(pump['matchLevel'], 'element')
        self.assertIn('tag', pump['reason'])
        facade = next(s for s in suggestions['suggestions'] if s.get('updateId') == 'demo-facade-blocked')
        self.assertEqual(facade['expressId'], 755)
        self.assertEqual(facade['matchLevel'], 'location')
        valid = {e['expressId']: e['globalId'] for e in model['elements']}
        for suggestion in suggestions['suggestions']:
            self.assertEqual(suggestion['review'], 'pending')
            self.assertEqual(valid[suggestion['expressId']], suggestion['globalId'])
            self.assertTrue(suggestion['reason'])
            self.assertNotIn('confidence', suggestion)
        after = self.workspace.state()
        self.assertEqual(after['modelLinks'], [])
        self.assertEqual(after['issues'], issues_before)
        self.assertEqual(suggestions, self.workspace.model_suggestions(model['id']))

    def test_unknown_and_ambiguous_references_stay_unmatched(self):
        _, model = self.load_model()
        elements = model['elements']
        first = next(e for e in elements if e['tag'] == 'P-02')
        duplicate = {**first, 'expressId': 99999, 'globalId': '0' * 22}
        source = {'text': 'Pump P-02 has an issue.', 'location': 'Basement'}
        self.assertIsNone(suggest_element(source, elements + [duplicate]))
        self.assertIsNone(suggest_element({'text': 'Unknown roof vent R-99 needs checking.', 'location': 'Unknown roof zone'}, elements))
        state = self.workspace.save_update({'projectId': PROJECT, 'type': 'note', 'text': 'Unknown roof vent R-99 needs checking.',
            'location': 'Unknown roof zone', 'date': self.demo['today'], 'time': '11:45', 'contributor': 'QA officer'})
        update = next(u for u in state['updates'] if u['contributor'] == 'QA officer')
        result = self.workspace.model_suggestions(model['id'])
        self.assertIn(update['id'], result['unmatchedUpdateIds'])
        self.assertFalse(any(s.get('updateId') == update['id'] for s in result['suggestions']))

    def test_confirmed_link_keeps_exact_ifc_identity_and_review_does_not_accept_issue(self):
        _, model = self.load_model()
        _, issue = self.report_issue()
        payload = {'modelId': model['id'], 'issueId': issue['id'], 'expressId': 683,
                   'globalId': 'forged-id', 'reviewer': 'QA officer', 'note': 'Officer checked P-02 model location.'}
        state = self.workspace.save_model_link(None, payload)
        link = state['modelLinks'][0]
        pump = next(e for e in model['elements'] if e['expressId'] == 683)
        self.assertEqual(link['globalId'], pump['globalId'])
        self.assertEqual(link['review'], 'accepted')
        self.assertEqual(link['sourceIds'], issue['sourceIds'])
        self.assertEqual(next(i for i in state['issues'] if i['id'] == issue['id'])['review'], issue['review'])
        state = self.workspace.save_model_link(None, payload)
        self.assertEqual(len(state['modelLinks']), 1)
        self.assertEqual(state['modelLinks'][0]['id'], link['id'])
        state = self.workspace.save_model_link(link['id'], {'expressId': 665, 'note': 'Changed to room-level context.'})
        self.assertEqual(state['modelLinks'][0]['matchLevel'], 'location')
        self.assertEqual(state['modelLinks'][0]['elementName'], 'Basement - Pump room')
        saved = Workspace(self.directory.name).state()['modelLinks'][0]
        self.assertEqual(saved['note'], 'Changed to room-level context.')
        self.assertEqual(saved['expressId'], 665)

    def test_cross_project_and_nonexistent_link_targets_are_rejected(self):
        _, model = self.load_model()
        _, issue = self.report_issue()
        base = {'modelId': model['id'], 'issueId': issue['id'], 'expressId': 683, 'reviewer': 'QA officer'}
        for changes in ({'expressId': 999999}, {'expressId': True}, {'expressId': '683.0'}, {'reviewer': ''}, {'issueId': 'missing'}):
            with self.subTest(changes=changes), self.assertRaises(ApiError):
                self.workspace.save_model_link(None, {**base, **changes})
        self.workspace.save_update({'projectId': 'workspace', 'type': 'note', 'text': 'Other project evidence.',
            'location': 'Other project', 'date': self.demo['today'], 'time': '12:00', 'contributor': 'Outside officer'})
        other = next(u for u in self.workspace.state()['updates'] if u['projectId'] == 'workspace')
        with self.assertRaises(ApiError):
            self.workspace.save_model_link(None, {'modelId': model['id'], 'updateId': other['id'], 'expressId': 683, 'reviewer': 'QA officer'})
        self.assertEqual(self.workspace.state()['modelLinks'], [])

    def test_express_ids_are_scoped_by_model_and_unlink_preserves_issue_and_evidence(self):
        _, first = self.load_model()
        _, issue = self.report_issue()
        pump = next(e for e in first['elements'] if e['expressId'] == 683)
        second_blob = DEMO.replace(pump['globalId'].encode(), b'1ABCDEFGHIJKLMNOPQRSTU')
        state = self.workspace.save_model({'projectId': PROJECT, 'file': {'name': 'revision.ifc'}}, blob=second_blob)
        second = next(m for m in state['models'] if m['id'] != first['id'])
        for model in (first, second):
            state = self.workspace.save_model_link(None, {'modelId': model['id'], 'issueId': issue['id'], 'expressId': 683, 'reviewer': 'QA officer'})
        links = state['modelLinks']
        self.assertEqual(len(links), 2)
        self.assertEqual(len({link['id'] for link in links}), 2)
        self.assertEqual(len({link['globalId'] for link in links}), 2)
        self.workspace.issue(issue['id'], {'status': 'Resolved'})
        state = self.workspace.delete_model_link(links[0]['id'])
        self.assertEqual(len(state['modelLinks']), 1)
        self.assertEqual(next(i for i in state['issues'] if i['id'] == issue['id'])['status'], 'Resolved')
        self.assertTrue(all(any(u['id'] == sid for u in state['updates']) for sid in issue['sourceIds']))
        self.assertEqual(len(Workspace(self.directory.name).state()['modelLinks']), 1)

    def test_http_model_bytes_suggestions_and_link_routes(self):
        # Exercise actual upload/download/DELETE routing without duplicating the
        # existing full report workflow suite.
        class LocalHandler(Handler):
            workspace = self.workspace
        server = ThreadingHTTPServer(('127.0.0.1', 0), LocalHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = 'http://127.0.0.1:' + str(server.server_port)
        def request(method, route, payload=None, raw=False):
            body = None if payload is None else json.dumps(payload).encode()
            query = urllib.request.Request(url + route, data=body, method=method,
                headers={'Content-Type': 'application/json'})
            with urllib.request.urlopen(query, timeout=10) as response:
                data = response.read()
                return data if raw else json.loads(data)
        try:
            state = request('POST', '/api/models', {'projectId': PROJECT, 'file': {
                'name': 'riverside.ifc', 'data': base64.b64encode(DEMO).decode()}})
            model = state['models'][0]
            self.assertEqual(request('GET', model['url'], raw=True), DEMO)
            result = request('GET', '/api/models/' + model['id'] + '/suggestions')
            self.assertTrue(result['suggestions'])
            state = request('POST', '/api/model-links', {'modelId': model['id'], 'updateId': 'demo-pump',
                'expressId': 683, 'reviewer': 'HTTP QA'})
            link = state['modelLinks'][0]
            state = request('PATCH', '/api/model-links/' + link['id'], {'note': 'Checked via HTTP'})
            self.assertEqual(state['modelLinks'][0]['note'], 'Checked via HTTP')
            state = request('DELETE', '/api/model-links/' + link['id'])
            self.assertEqual(state['modelLinks'], [])
            self.assertEqual(request('GET', model['url'], raw=True), DEMO)
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=2)


if __name__ == '__main__':
    unittest.main()
