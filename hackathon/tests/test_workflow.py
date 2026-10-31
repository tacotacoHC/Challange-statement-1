"""End-to-end API checks against isolated, durable workspaces (stdlib only)."""
import base64
import copy
import datetime
import json
import io
import os
import re
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
import wave

ROOT = Path(__file__).resolve().parents[1]
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8i8AAAAASUVORK5CYII=')


class SiteBriefWorkflow(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='sitebrief-test-')
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.url = 'http://127.0.0.1:%s' % self.port
        self.start_server()

    def start_server(self):
        env = dict(os.environ, SITEBRIEF_DATA_DIR=self.directory.name)
        for variable in ('OPENAI_API_KEY', 'GEMINI_API_KEY', 'GOOGLE_API_KEY', 'AI_PROVIDER'):
            env.pop(variable, None)
        env['AI_PROVIDER'] = 'demo'
        self.process = subprocess.Popen(
            [sys.executable, str(ROOT / 'server.py'), '--port', str(self.port)],
            cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        for _ in range(100):
            if self.process.poll() is not None:
                self.fail('Server stopped during startup: ' + self.process.stderr.read().decode())
            try:
                self.request('GET', '/api/state')
                return
            except (OSError, urllib.error.URLError):
                time.sleep(.05)
        self.fail('Server did not become ready')

    def stop_server(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
        self.process.stderr.close()

    def tearDown(self):
        self.stop_server()
        self.directory.cleanup()

    def request(self, method, path, payload=None, raw=False):
        data = None if payload is None else json.dumps(payload).encode()
        req = urllib.request.Request(self.url + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(req, timeout=15) as response:
            body = response.read()
            return body if raw else json.loads(body)

    def demo_report(self):
        state = self.request('POST', '/api/demo', {})
        project = next(p for p in state['projects'] if p['id'] != 'workspace')
        state = self.request('POST', '/api/reports/generate', {
            'projectId': project['id'], 'date': state['today']})
        report = next(r for r in state['reports'] if r['projectId'] == project['id'])
        return state, report

    def test_updates_files_and_original_evidence_survive_server_restart(self):
        state = self.request('GET', '/api/state')
        original = 'Ground floor screed completed. Pump inspection is still pending.'
        state = self.request('POST', '/api/updates', {
            'projectId': 'workspace', 'location': 'Ground floor', 'date': state['today'],
            'time': '09:30', 'contributor': 'Test officer', 'type': 'photo',
            'text': original, 'caption': 'Screed evidence', 'discipline': 'Indoor',
            'files': [{'name': 'site.png', 'mime': 'image/png',
                       'data': base64.b64encode(PNG).decode()}]})
        update = next(u for u in state['updates'] if u.get('contributor') == 'Test officer')
        update_id = update['id']
        self.assertEqual(state['activity'][0]['projectId'], 'workspace')
        self.request('PATCH', '/api/updates/' + update_id, {'caption': 'Reviewed site caption'})
        self.stop_server()
        self.start_server()
        saved = next(u for u in self.request('GET', '/api/state')['updates'] if u['id'] == update_id)
        self.assertEqual(saved['caption'], 'Reviewed site caption')
        self.assertEqual(saved['text'], original)
        self.assertTrue(saved.get('files'), 'Upload metadata must remain linked to evidence')
        descriptor = saved['files'][0]
        file_url = descriptor.get('url') or '/files/' + descriptor['id']
        self.assertEqual(self.request('GET', file_url, raw=True), PNG)
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request('POST', '/api/updates', {
                'projectId': 'workspace', 'date': state['today'], 'time': '10:00',
                'contributor': 'Test officer', 'type': 'photo',
                'files': [{'name': 'program.exe', 'mime': 'application/x-msdownload', 'data': 'YWJj'}]})
        self.assertEqual(error.exception.code, 400)

    def test_recording_uses_manual_transcript_without_claiming_analysis(self):
        stream = io.BytesIO()
        with wave.open(stream, 'wb') as recording:
            recording.setnchannels(1)
            recording.setsampwidth(2)
            recording.setframerate(8000)
            recording.writeframes(b'\x00\x00' * 800)
        audio = stream.getvalue()
        state = self.request('GET', '/api/state')
        state = self.request('POST', '/api/updates', {
            'projectId': 'workspace', 'location': 'Basement', 'date': state['today'],
            'time': '11:00', 'contributor': 'Voice contributor', 'type': 'audio',
            'files': [{'name': 'recording.wav', 'mime': 'audio/wav',
                       'data': base64.b64encode(audio).decode()}]})
        update = next(u for u in state['updates'] if u['contributor'] == 'Voice contributor')
        self.assertFalse(update.get('transcript'), 'Offline mode must not invent a transcript')
        self.assertTrue(update.get('qualityAlerts'), 'Manual transcription needs a clear prompt')
        self.assertEqual(self.request('GET', update['files'][0]['url'], raw=True), audio)
        state = self.request('PATCH', '/api/updates/' + update['id'], {
            'transcript': 'Pump maintenance is ongoing in the basement.'})
        saved = next(u for u in state['updates'] if u['id'] == update['id'])
        self.assertEqual(saved['transcript'], 'Pump maintenance is ongoing in the basement.')
        self.assertEqual(saved['original']['transcript'], '')

    def test_evidence_review_approval_and_pdf_match_saved_content(self):
        state, report = self.demo_report()
        self.assertEqual(report['status'], 'Draft')
        self.assertEqual(state['metrics']['urgentReview'], 1, 'Routine High-priority follow-up must not count as urgent human review')
        valid_sources = {u['id'] for u in state['updates']}
        self.assertTrue(report['findings'])
        for finding in report['findings']:
            if finding['kind'] == 'fact':
                self.assertTrue(finding['sourceIds'], finding)
            self.assertTrue(set(finding['sourceIds']).issubset(valid_sources), finding)
        # Approval cannot bypass the officer's pending review.
        with self.assertRaises(urllib.error.HTTPError):
            self.request('POST', '/api/reports/' + report['id'] + '/approve', {'reviewer': 'QA Officer'})
        reviewed = copy.deepcopy(report['findings'])
        for finding in reviewed:
            finding['review'] = 'accepted'
        fact = next(f for f in reviewed if f['kind'] == 'fact')
        fact['text'] = 'Reviewed completion record confirmed by QA officer.'
        state = self.request('PATCH', '/api/reports/' + report['id'], {'findings': reviewed})
        saved = next(r for r in state['reports'] if r['id'] == report['id'])
        self.assertEqual(next(f for f in saved['findings'] if f['id'] == fact['id'])['text'], fact['text'])
        state = self.request('POST', '/api/reports/' + report['id'] + '/approve', {'reviewer': 'QA Officer'})
        approved = next(r for r in state['reports'] if r['id'] == report['id'])
        self.assertEqual(approved['status'], 'Approved')
        self.assertEqual(approved['reviewer'], 'QA Officer')
        self.assertTrue(approved['approvedAt'])
        self.assertTrue(all(event.get('projectId') == report['projectId'] for event in state['activity']),
                        'Daily reporting activity must remain scoped to its saved project')
        pdf = self.request('GET', '/api/reports/' + report['id'] + '/pdf', raw=True)
        self.assertTrue(pdf.startswith(b'%PDF-'))
        self.assertIn(b'Reviewed completion record', pdf)
        self.assertIn(b'QA Officer', pdf)
        facade_refs = re.findall(rb'\((E-[^|]+) \| CHAT \| (?:Ethan Chen|Nora Aziz)', pdf)
        self.assertEqual(len(facade_refs), 2, 'Both conflicting facade sources must appear in the evidence register')
        self.assertNotEqual(facade_refs[0], facade_refs[1], 'Conflicting sources need distinct evidence references')
        self.assertGreater(len(pdf), 1500)
        reviewed[0]['text'] += ' Updated after approval.'
        state = self.request('PATCH', '/api/reports/' + report['id'], {'findings': reviewed})
        draft = next(r for r in state['reports'] if r['id'] == report['id'])
        self.assertEqual(draft['status'], 'Draft')
        with self.assertRaises(urllib.error.HTTPError):
            self.request('GET', '/api/reports/' + report['id'] + '/pdf', raw=True)

    def test_reprocessing_preserves_reviews_and_never_duplicates_work(self):
        state, report = self.demo_report()
        no_owner = [a for a in state['actions'] if not a.get('owner') and not a.get('deadline')]
        self.assertTrue(no_owner, 'An unspecified owner and deadline must remain unset')
        actions_before = {a['id'] for a in state['actions']}
        issues_before = {i['id'] for i in state['issues']}
        reviewed = copy.deepcopy(report['findings'])
        reviewed[0]['text'] = 'Reviewer correction must survive regeneration.'
        reviewed[0]['review'] = 'accepted'
        self.request('PATCH', '/api/reports/' + report['id'], {'findings': reviewed})
        for _ in range(2):
            state = self.request('POST', '/api/reports/generate', {
                'projectId': report['projectId'], 'date': report['date']})
        self.assertEqual({a['id'] for a in state['actions']}, actions_before)
        self.assertEqual({i['id'] for i in state['issues']}, issues_before)
        saved = next(r for r in state['reports'] if r['id'] == report['id'])
        self.assertEqual(next(f for f in saved['findings'] if f['id'] == reviewed[0]['id'])['text'], reviewed[0]['text'])
        self.assertEqual(len(self.request('POST', '/api/demo', {})['updates']), len(state['updates']))

    def test_action_review_stays_in_sync_with_report_and_approval(self):
        state, report = self.demo_report()
        proposed = next(f for f in report['findings'] if f.get('actionKey'))
        proposed['text'] = 'Reviewer corrected the scope: inspect pump before authorizing restart.'
        proposed['review'] = 'accepted'
        state = self.request('PATCH', '/api/reports/' + report['id'], {'findings': [proposed]})
        action = next(a for a in state['actions'] if a.get('key') == proposed['actionKey'])
        self.assertEqual(action['review'], 'accepted')
        self.assertEqual(action['description'], proposed['text'])
        linked_issue = next(i for i in state['issues'] if i['id'] == action['issueId'])
        self.assertEqual(linked_issue['review'], 'accepted')
        report = next(r for r in state['reports'] if r['id'] == report['id'])
        for finding in report['findings']:
            finding['review'] = 'accepted'
        self.request('PATCH', '/api/reports/' + report['id'], {'findings': report['findings']})
        self.request('POST', '/api/reports/' + report['id'] + '/approve', {'reviewer': 'QA Officer'})
        state = self.request('PATCH', '/api/actions/' + action['id'], {'review': 'rejected'})
        report = next(r for r in state['reports'] if r['id'] == report['id'])
        synced = next(f for f in report['findings'] if f['id'] == proposed['id'])
        self.assertEqual(synced['review'], 'rejected')
        self.assertEqual(report['status'], 'Draft')
        self.assertFalse(report.get('approvalSnapshot'))
        state = self.request('PATCH', '/api/reports/' + report['id'], {'findings': report['findings']})
        self.assertEqual(next(a for a in state['actions'] if a['id'] == action['id'])['review'], 'rejected')

    def test_resolved_work_is_not_overdue_and_metrics_use_saved_records(self):
        state = self.request('GET', '/api/state')
        yesterday = (datetime.date.fromisoformat(state['today']) - datetime.timedelta(days=1)).isoformat()
        state = self.request('POST', '/api/actions', {
            'title': 'Inspect pump', 'description': 'Check the reported fault',
            'projectId': 'workspace', 'location': 'Ground floor', 'owner': '',
            'deadline': yesterday, 'priority': 'High', 'status': 'Open', 'sourceIds': []})
        action = next(a for a in state['actions'] if a['title'] == 'Inspect pump')
        metrics_before = state['metrics']
        overdue_key = next(k for k in metrics_before if 'overdue' in k.lower())
        self.assertEqual(metrics_before[overdue_key], 1)
        state = self.request('PATCH', '/api/actions/' + action['id'], {'status': 'Resolved'})
        self.assertEqual(state['metrics'][overdue_key], 0)
        self.stop_server()
        self.start_server()
        state = self.request('GET', '/api/state')
        self.assertEqual(next(a for a in state['actions'] if a['id'] == action['id'])['status'], 'Resolved')
        self.assertEqual(state['metrics'][overdue_key], 0)


if __name__ == '__main__':
    unittest.main()
