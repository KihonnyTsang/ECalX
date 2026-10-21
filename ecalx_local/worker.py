"""One local task executor. Never retry a failed paid task."""
import fcntl
import json
import os
import sys
from . import PERSONAL
from .engine import LocalAgent, LocalWorkflow, LiveClient, online_quote
from ecalx.agent.config import load_local_env
from ecalx.agent.siliconflow import LLMBudget


def run(tid, storage):
    agent = LocalAgent(storage)
    task = agent.get(tid)
    lock = agent.storage_root/(tid+'.worker.lock')
    state = agent.storage_root/(tid+'.worker.json')
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        def mark(value):
            temp = state.with_suffix('.tmp')
            temp.write_text(json.dumps({'state':value, 'pid':os.getpid()}))
            temp.replace(state)
        mark('running')
        try:
            if task['client_mode']=='LIVE_LLM':
                # Only reached after this particular user's explicit online consent.
                if not task.get('online_consent') or task['online_consent'] != online_quote(check_usage=False):
                    raise ValueError('在线预算已改变或未确认，请新建任务')
                if not PERSONAL:
                    from .configuration import load_credentials
                    load_credentials()
                else:
                    load_local_env(agent.root)  # Backend only: never return credentials to UI/logs.
                client = LiveClient(budget={k:v for k,v in task['limits'].items()
                                          if k in LLMBudget.model_fields},
                                    prices=task['online_consent'].get('prices') if PERSONAL else None)
                client.bind(agent, tid)
            else:
                client = LocalWorkflow()
            for _ in range(task['limits']['steps']):
                current = agent.get(tid)
                if current['status'] not in {'DRAFT','RUNNING'} or (current['status']=='DRAFT' and current['plan']):
                    break
                agent.advance(tid, client)
                if agent.get(tid)['status'] != 'RUNNING':
                    break
        except Exception:
            # Exception bodies can reflect secrets; keep only a generic host message.
            current = agent.get(tid)
            if current['status'] not in {'COMPLETED','FAILED','CANCELLED','PAUSED'}:
                current.update(status='FAILED', error='任务进程无法继续；请检查本地配置、共享API限制或高级详情。')
                agent.save(current)
        finally:
            mark('stopped')


if __name__ == '__main__':
    run(sys.argv[1], sys.argv[2])
