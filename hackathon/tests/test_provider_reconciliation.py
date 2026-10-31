"""Switching extraction providers retains evidence-linked reviewed records."""
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


class ProviderReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory(prefix='sitebrief-reconcile-')
        self.environment=patch.dict(os.environ,{'AI_PROVIDER':'demo','OPENAI_API_KEY':'','GEMINI_API_KEY':'','GOOGLE_API_KEY':''})
        self.environment.start()
        self.workspace=Workspace(self.directory.name)
        self.state=self.workspace.load_demo()
        self.request={'projectId':'project-riverside','date':self.state['today']}
        updates=[u for u in self.state['updates'] if u['projectId']=='project-riverside']
        self.baseline=ai_service._offline(updates)

    def tearDown(self):
        self.environment.stop()
        self.directory.cleanup()

    def changed_provider_result(self):
        result=copy.deepcopy(self.baseline)
        issue_alias={i['key']:'google-'+i['key'] for i in result['issues']}
        action_alias={a['key']:'google-'+a['key'] for a in result['actions']}
        for issue in result['issues']:
            issue['key']=issue_alias[issue['key']];issue['id']=issue['key'];issue['title']='Provider changed issue label'
        for action in result['actions']:
            action['key']=action_alias[action['key']];action['id']=action['key'];action['title']='Provider changed action label'
            action['issueKey']=issue_alias[action['issueKey']]
        for finding in result['findings']:
            finding['key']='google-'+finding['key'];finding['id']=finding['key'];finding['text']='Provider rewritten: '+finding['text']
            if finding.get('issueKey'):finding['issueKey']=issue_alias[finding['issueKey']]
            if finding.get('actionKey'):finding['actionKey']=action_alias[finding['actionKey']]
        result['mode']='AI connected'
        return result

    def test_provider_switch_keeps_ids_assignments_edits_and_finding_references(self):
        with patch.object(ai_service,'generate_findings',side_effect=[self.baseline,self.changed_provider_result()]) as generate:
            with patch.object(ai_service,'configuration',return_value={'provider':'demo','mode':'Demo mode'}):
                initial=self.workspace.generate(self.request)
            report=initial['reports'][0]
            proposal=copy.deepcopy(next(f for f in report['findings'] if f.get('actionKey')))
            proposal.update(text='Officer corrected scope: inspect and report the findings.',review='accepted')
            state=self.workspace.edit_report(report['id'],{'findings':[proposal]})
            action=next(a for a in state['actions'] if a['key']==proposal['actionKey'])
            state=self.workspace.action(action['id'],{'owner':'Apex Civil','title':'Officer confirmed inspection'})
            issue=next(i for i in state['issues'] if i['key']==proposal['issueKey'])
            self.workspace.issue(issue['id'],{'category':'Coordination','priority':'Low'})
            with patch.object(ai_service,'configuration',return_value={'provider':'google','mode':'AI connected','textModel':'gemini-test'}):
                final=self.workspace.generate(self.request)
                final=self.workspace.generate(self.request)
            self.assertEqual(generate.call_count,2)
            self.assertEqual({a['id'] for a in initial['actions']},{a['id'] for a in final['actions']})
            self.assertEqual({i['id'] for i in initial['issues']},{i['id'] for i in final['issues']})
            self.assertEqual(len(final['reports'][0]['findings']),len(report['findings']))
            preserved_action=next(a for a in final['actions'] if a['id']==action['id'])
            self.assertEqual(preserved_action['owner'],'Apex Civil')
            self.assertEqual(preserved_action['title'],'Officer confirmed inspection')
            self.assertEqual(preserved_action['description'],proposal['text'])
            preserved_issue=next(i for i in final['issues'] if i['id']==issue['id'])
            self.assertEqual(preserved_issue['category'],'Coordination')
            self.assertEqual(preserved_issue['priority'],'Low')
            preserved=next(f for f in final['reports'][0]['findings'] if f['id']==proposal['id'])
            self.assertEqual(preserved['text'],proposal['text'])
            self.assertEqual(preserved['review'],'accepted')
            self.assertEqual(preserved['actionKey'],action['key'])
            self.assertEqual(preserved['issueKey'],issue['key'])
            issue_keys={i['key'] for i in final['issues']};action_keys={a['key'] for a in final['actions']}
            for finding in final['reports'][0]['findings']:
                if finding.get('issueKey'):self.assertIn(finding['issueKey'],issue_keys)
                if finding.get('actionKey'):self.assertIn(finding['actionKey'],action_keys)

    def test_ambiguous_new_issues_are_not_merged_into_one_existing_record(self):
        with patch.object(ai_service,'generate_findings',return_value=self.baseline):
            initial=self.workspace.generate(self.request)
        result=self.changed_provider_result()
        duplicated=copy.deepcopy(result['issues'][0]);duplicated['key']='separate-issue';duplicated['id']='separate-issue'
        result['issues'].append(duplicated)
        reconciled=self.workspace.reconcile_extraction(result,initial['reports'][0]['id'],initial['reports'][0]['findings'])
        self.assertEqual(len(reconciled['issues']),len(result['issues']))
        self.assertNotEqual(reconciled['issues'][0]['key'],initial['issues'][0]['key'])
        self.assertEqual(reconciled['issues'][-1]['key'],'separate-issue')


if __name__=='__main__':unittest.main()
