import unittest
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from llm import parse_analysis
except ImportError:
    parse_analysis = None

class AnalysisTests(unittest.TestCase):
    def test_valid_grounded_json_is_preserved(self):
        self.assertIsNotNone(parse_analysis, 'analysis parser must exist')
        import json
        data = dict(remote_policy='hybrid', remote_evidence='Télétravail partiel', role_category='devops', role_evidence='Ingénieur DevOps', contract_types=['contractor'], seniority='senior', skills=[{'name':'Kubernetes','evidence':'Kubernetes'}], summary='Build deployment automation.')
        text='Title: Ingénieur DevOps\nRemote: Télétravail partiel\nContracts: contractor\nStack: Kubernetes'
        self.assertEqual(parse_analysis(json.dumps(data), text)['role_category'], 'devops')

    def test_hallucinated_skill_is_rejected(self):
        import json
        data = dict(remote_policy='full',remote_evidence='Full remote',role_category='other',role_evidence='',contract_types=[],seniority=None,skills=[{'name':'Rust','evidence':'Rust'}],summary='A job')
        with self.assertRaises(ValueError):
            parse_analysis(json.dumps(data), 'Full remote. Python developer.')

    def test_filter_fields_must_be_valid_and_grounded(self):
        import json
        base = dict(remote_policy='full',remote_evidence='Full remote',role_category='other',role_evidence='',contract_types=[],seniority=None,skills=[],summary='A job')
        for change in [dict(remote_policy='sometimes'), dict(role_category='sysadmin'), dict(remote_evidence='work from home'), dict(role_category='devops',role_evidence='DevOps'), dict(contract_types='CDI'), dict(skills='Python')]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                parse_analysis(json.dumps({**base, **change}), 'Full remote. Python developer.')

    def test_analyzer_calls_only_requested_lunar_model(self):
        import json
        import llm
        self.assertTrue(hasattr(llm, 'analyze_offer'), 'analyzer must exist')
        calls=[]
        data=dict(remote_policy='hybrid',remote_evidence='partial',role_category='devops',role_evidence='DevOps',contract_types=['contractor'],seniority=None,skills=[{'name':'Docker','evidence':'Docker'}],summary='Maintain deployment tooling.')
        def request(**kwargs):
            calls.append(kwargs)
            return json.dumps(data), {'prompt_tokens':100,'completion_tokens':50}
        result=llm.analyze_offer(dict(title='DevOps',remote_raw='partial',contracts_raw=['contractor'],skills_raw=['Docker'],description='Docker deployment.'), request=request)
        self.assertEqual(calls[0]['model'], 'gpt-6-luna')
        self.assertEqual(result['model'], 'gpt-6-luna')
        self.assertEqual(result['usage']['prompt_tokens'],100)
        self.assertIn('untrusted',calls[0]['messages'][0]['content'].lower())

    def test_invalid_extraction_is_retried_once_with_same_model(self):
        import json, llm
        calls=[]
        good=dict(remote_policy='unknown',remote_evidence='',role_category='other',role_evidence='',contract_types=[],seniority=None,skills=[],summary='No telework statement.')
        def request(**kwargs):
            calls.append(kwargs)
            return ('not JSON' if len(calls)==1 else json.dumps(good)), {'prompt_tokens':10,'completion_tokens':2}
        result=llm.analyze_offer(dict(title='A job',description='No telework statement.'),request=request)
        self.assertEqual(len(calls),2)
        self.assertEqual(result['usage']['prompt_tokens'],20)
        self.assertEqual({c['model'] for c in calls},{'gpt-6-luna'})

if __name__ == '__main__': unittest.main()
