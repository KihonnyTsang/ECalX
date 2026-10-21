"""Process boundary for responsive controls and original main-thread deadlines."""
import json
import os
import subprocess
import sys
from pathlib import Path
from . import APP_ROOT, STORAGE, PERSONAL
from .engine import LocalAgent, BUDGET
from ecalx.agent.config import MODEL


class LiveIdentity:
    mode, model, provider = 'LIVE_LLM', MODEL, 'siliconflow'
    budget = BUDGET


def live_identity():
    identity=LiveIdentity()
    if not PERSONAL:
        from .configuration import read_config, configured_budget
        identity.model=read_config()['model']
        identity.budget=configured_budget()
    return identity


def create_research(prepared, mode, *, confirmed=False, consent=None, storage=None):
    if not confirmed:
        raise ValueError('请确认生效条件；未指定项不会添加限制')
    if mode not in {'local', 'live'}:
        raise ValueError('未知研究模式')
    from .engine import LocalWorkflow, online_quote
    if mode == 'live':
        quote = online_quote()
        if consent != quote:
            raise ValueError('请明确同意当前在线预算和数据发送范围')
    parent = None
    if prepared.get('parent_task_id'):
        from .conversation import read_task, fingerprint
        parent = read_task(prepared['parent_task_id'], storage)
        if fingerprint(parent) != prepared.get('parent_sha256'):
            raise ValueError('来源研究已变化，请重新核对新任务草稿')
    engine = LocalAgent(storage)
    identity = live_identity() if mode=='live' else LocalWorkflow()
    task = engine.create_task(prepared['raw'].get('natural_language', ''),
                              identity, budget=identity.budget if mode=='live' else BUDGET)
    engine.establish_goals(task['id'], prepared['goals'], confirmed=True)
    task = engine.get(task['id'])
    task['input_snapshot'] = prepared
    task['online_consent'] = consent if mode=='live' else None
    task['app_mode'] = mode
    task['optional_preferences'] = prepared['pending']
    if parent:
        task['parent_research'] = {'task_id':parent['id'], 'snapshot_sha256':prepared['parent_sha256']}
    engine.save(task)
    engine.log(task['id'], 'LOCAL_APP_INPUT_CONFIRMED', {'mode':mode, 'online_consent':consent})
    return engine.get(task['id'])


def launch(tid, storage=None):
    engine = LocalAgent(storage)
    task = engine.get(tid)
    if task['status'] not in {'DRAFT','RUNNING'} or (task['status']=='DRAFT' and task['plan']):
        return
    # Worker performs its own nonblocking lease; double clicks cannot double-send.
    subprocess.Popen([sys.executable, '-m', 'ecalx_local.worker', tid, str(engine.storage_root)],
                     cwd=APP_ROOT, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True,
                     env={**os.environ, 'PYTHONDONTWRITEBYTECODE':'1'})


def worker_status(tid, storage=None):
    p = Path(storage or STORAGE) / (tid+'.worker.json')
    if not p.exists():
        return {'state':'idle'}
    result = json.loads(p.read_text())
    if result['state']=='running':
        try:
            os.kill(result['pid'], 0)
        except ProcessLookupError:
            result['state']='interrupted'
    return result
