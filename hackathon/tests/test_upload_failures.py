"""Saving evidence does not depend on AI availability or valid AI credentials."""
import base64
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ai_service
from server import Workspace

PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a8i8AAAAASUVORK5CYII=')

class EntrySaveFailureTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix='sitebrief-save-failure-')
        self.environment=patch.dict(os.environ,{'AI_PROVIDER':'google','GEMINI_API_KEY':'mock-blocked-key','GOOGLE_API_KEY':'','OPENAI_API_KEY':''})
        self.environment.start()
        self.workspace=Workspace(self.directory.name)
        self.payload={'projectId':'workspace','location':'Block A','date':self.workspace.state()['today'],'time':'09:30','contributor':'Test officer','type':'note','text':'Ground floor work completed.'}
    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()
    def test_note_save_needs_no_ai_request_and_survives_restart(self):
        with patch.object(ai_service,'analyze_media') as media,patch.object(ai_service,'generate_findings') as extract:
            state=self.workspace.save_update(self.payload)
            media.assert_not_called()
            extract.assert_not_called()
        saved=state['updates'][0]
        self.assertEqual(saved['original']['text'],self.payload['text'])
        restarted=Workspace(self.directory.name)
        self.assertEqual(restarted.state()['updates'][0]['id'],saved['id'])
    def test_rejected_ai_key_preserves_uploaded_photo_and_manual_caption(self):
        payload={**self.payload,'type':'photo','text':'','caption':'Officer-provided site photo caption.','files':[{'name':'evidence.png','mime':'image/png','data':base64.b64encode(PNG).decode()}]}
        with patch.object(ai_service,'analyze_media',side_effect=RuntimeError('Google Gemini returned HTTP 403. API key service blocked.')):
            state=self.workspace.save_update(payload)
        saved=state['updates'][0]
        self.assertEqual(saved['processingStatus'],'failed')
        self.assertEqual(saved['caption'],payload['caption'])
        self.assertEqual(saved['original']['caption'],payload['caption'])
        self.assertTrue(saved['files'])
        self.assertEqual((Path(self.directory.name)/'uploads'/saved['files'][0]['storedName']).read_bytes(),PNG)
        self.assertTrue(any(a['type']=='processing_failed' for a in saved['qualityAlerts']))
        self.assertEqual(Workspace(self.directory.name).state()['updates'][0]['id'],saved['id'])

if __name__=='__main__':unittest.main()
