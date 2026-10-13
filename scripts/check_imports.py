"""AST closure plus actual imports; emits relative module origins, never API calls."""
import ast
import importlib
import importlib.util
import json
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'src'), str(ROOT/'app')]


def main():
    def denied(*a, **k):
        raise AssertionError('Import verification must not use network')
    socket.socket.connect = socket.socket.connect_ex = socket.getaddrinfo = denied
    import sqlite3
    sqlite3.connect = denied
    modules = {}
    for p in sorted((ROOT/'src').rglob('*.py')):
        name = '.'.join(p.relative_to(ROOT/'src').with_suffix('').parts)
        if name.endswith('.__init__'):
            name = name[:-9]
        modules[name] = p
    modules['portfolio_demo'] = ROOT/'app/portfolio_demo.py'
    units = dict(modules)
    for directory in ('app', 'scripts', 'tests'):
        for p in sorted((ROOT/directory).rglob('*.py')):
            if p not in modules.values():
                units[directory+'.'+p.stem] = p
    graph = []
    for name, p in units.items():
        tree = ast.parse(p.read_text())
        package = name if p.name == '__init__.py' else name.rpartition('.')[0]
        for node in ast.walk(tree):
            targets = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                target = node.module or ''
                if node.level:
                    target = importlib.util.resolve_name('.'*node.level+target, package)
                targets = [target]
            for target in targets:
                if target.startswith('ecalx') and target not in modules:
                    raise AssertionError('Missing local dependency: '+target)
                graph.append({'from':name, 'to':target})
    for name in modules:
        importlib.import_module(name)
    # Exercise deferred imports and validators rather than relying on import syntax alone.
    from ecalx.agent.contracts import Goals, VirtualPlan, definitions
    Goals(exclude_elements=['Pb']).request()
    VirtualPlan()
    for planning in (False, True):
        definitions(planning=planning, frozen_goals={})
    local = {}
    for name, module in sorted(sys.modules.items()):
        if name == 'ecalx' or name.startswith('ecalx.'):
            origin = Path(module.__file__).resolve()
            assert origin.is_relative_to((ROOT/'src').resolve()), 'Foreign ECalX import'
            local[name] = str(origin.relative_to(ROOT))
    forbidden = ['ecalx.ml', 'ecalx.experiments.virtual', 'ecalx.experiments.real', 'ecalx.data.repository']
    assert not any(n in sys.modules for n in forbidden)
    print(json.dumps({'status':'PASSED','all_python_files_parsed':len(units),
                      'static_edges':graph,'actual_local_modules':local,
                      'deferred_validators_exercised':True,'forbidden_modules_absent':forbidden,
                      'dependencies':'active interpreter; installation provenance reported separately'},indent=2))


if __name__ == '__main__':
    main()
