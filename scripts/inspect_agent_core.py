"""Entry B: inspect real code without constructing a client or task store."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for filename in ('siliconflow.py', 'runtime.py', 'contracts.py', 'planning.py',
                 'capabilities.py', 'api_policy.py', 'faithfulness.py'):
    path = ROOT/'src/ecalx/agent'/filename
    tree = ast.parse(path.read_text())
    print('src/ecalx/agent/'+filename)
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            print('  '+str(node.lineno)+': '+node.name)
print('Source inspection only. Offline tests: python -B -m pytest -q tests/test_agent_core.py')
