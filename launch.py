"""Run project scripts in the installed Hermes dependency environment."""
import json
import os
from pathlib import Path
import subprocess
import sys


def runtime_command(script, args=(), resolver=subprocess.check_output):
    command = json.loads(resolver(['hermes','--print-runtime-command'],text=True))
    if not isinstance(command,list) or '-c' not in command:
        raise RuntimeError('Unexpected Hermes runtime launcher')
    index=command.index('-c')+1
    old="runpy.run_module('hermes_cli.main', run_name='__main__', alter_sys=True)"
    if old not in command[index]:
        raise RuntimeError('Hermes runtime launcher changed; review before running')
    command[index]=command[index].replace(old,"script=sys.argv.pop(1); sys.path.insert(0, os.path.dirname(os.path.abspath(script))); runpy.run_path(script, run_name='__main__')")
    return command + [str(script),*args]

if __name__=='__main__':
    if len(sys.argv)<2:
        raise SystemExit('usage: python launch.py SCRIPT [ARGS...]')
    command=runtime_command(Path(sys.argv[1]).resolve(),sys.argv[2:])
    os.execvp(command[0],command)
