import json, unittest, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    import launch
except ImportError:
    launch=None

class LauncherTests(unittest.TestCase):
    def test_runtime_keeps_hermes_bootstrap_and_runs_project_entry(self):
        self.assertIsNotNone(launch,'launcher must exist')
        def resolve(args,**kw):
            return json.dumps(['/python','-I','-c',"import sys,runpy;import hermes_bootstrap;runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)"])
        cmd=launch.runtime_command('/project/main.py',['collect'],resolver=resolve)
        self.assertIn('hermes_bootstrap',cmd[3])
        self.assertIn('run_path',cmd[3])
        self.assertIn('sys.argv.pop(1)',cmd[3])
        self.assertEqual(cmd[-2:],['/project/main.py','collect'])

if __name__=='__main__':unittest.main()
