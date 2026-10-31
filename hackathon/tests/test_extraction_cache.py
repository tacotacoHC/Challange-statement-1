"""Extraction caching protects durable records against stochastic reprocessing."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ai_service
from server import Workspace


class ExtractionCacheTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix='sitebrief-cache-')
        self.environment=patch.dict(os.environ,{'AI_PROVIDER':'demo','GEMINI_API_KEY':'','GOOGLE_API_KEY':'','OPENAI_API_KEY':'','OPENAI_TEXT_MODEL':'test-model-a','OPENAI_VISION_MODEL':'','OPENAI_TRANSCRIBE_MODEL':''})
        self.environment.start()
        self.workspace=Workspace(self.directory.name)
        self.state=self.workspace.load_demo()
        self.request={'projectId':'project-riverside','date':self.state['today']}

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def test_unchanged_evidence_reuses_extraction_after_restart_and_preserves_reviews(self):
        with patch.object(ai_service,'generate_findings',wraps=ai_service.generate_findings) as generate:
            state=self.workspace.generate(self.request)
            report=state['reports'][0]
            finding=copy.deepcopy(report['findings'][0])
            finding.update(text='Officer corrected this finding.',review='accepted')
            self.workspace.edit_report(report['id'],{'findings':[finding]})
            original_issues={i['id'] for i in state['issues']}
            original_actions={a['id'] for a in state['actions']}
            # A new Workspace object represents a restarted server reading SQLite.
            restarted=Workspace(self.directory.name)
            state=restarted.generate(self.request)
            state=restarted.generate(self.request)
            self.assertEqual(generate.call_count,1)
            self.assertEqual({i['id'] for i in state['issues']},original_issues)
            self.assertEqual({a['id'] for a in state['actions']},original_actions)
            reviewed=next(f for f in state['reports'][0]['findings'] if f['id']==finding['id'])
            self.assertEqual(reviewed['text'],finding['text'])
            self.assertEqual(reviewed['review'],'accepted')
            self.assertTrue(state['reports'][0]['reusedExtraction'])
            self.assertNotIn('extractions',state)

    def test_source_edits_and_model_configuration_invalidate_cache(self):
        with patch.object(ai_service,'generate_findings',wraps=ai_service.generate_findings) as generate:
            self.workspace.generate(self.request)
            self.workspace.edit_update('demo-photo',{'caption':'Corrected manual evidence caption supplied by an officer.'})
            self.workspace.generate(self.request)
            self.assertEqual(generate.call_count,2)
            with patch.dict(os.environ,{'OPENAI_TEXT_MODEL':'test-model-b'}):
                self.workspace.generate(self.request)
            self.assertEqual(generate.call_count,3)

    def test_processing_failure_is_not_cached_so_retry_can_recover(self):
        updates=[u for u in self.workspace.state()['updates'] if u['projectId']=='project-riverside']
        success=ai_service.generate_findings(updates)
        failed=copy.deepcopy(success)
        failed['processingError']='Temporary AI service failure'
        with patch.object(ai_service,'generate_findings',side_effect=[failed,success]) as generate:
            first=self.workspace.generate(self.request)
            self.assertTrue(first['reports'][0]['processingError'])
            recovered=self.workspace.generate(self.request)
            self.assertFalse(recovered['reports'][0]['processingError'])
            self.workspace.generate(self.request)
            self.assertEqual(generate.call_count,2)


if __name__=='__main__':unittest.main()
