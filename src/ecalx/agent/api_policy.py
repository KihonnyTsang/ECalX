"""Injectable transport policy interface; personal policy remains in original workspace."""
import signal
import threading
from contextlib import contextmanager
from .llm import ProviderError

class ProjectAPIPolicy:
    """Compatibility interface for the existing SiliconFlow transport."""
    def before_send(self,client,raw,timeout):
        raise ProviderError('必须注入当前用户的请求策略',code='EGRESS_PROFILE_REQUIRED')
    def finish(self,rid,attempt):
        raise NotImplementedError
    def halt(self,reason,attempts=()):
        pass

def settings(*args,**kwargs):
    raise ProviderError('公开模式使用用户配置',code='INVALID_CONFIG')
def budget_status(*args,**kwargs):
    raise ProviderError('公开模式使用本地账本',code='INVALID_CONFIG')
def fail(code):
    raise ProviderError('请求策略拒绝发送',code=code)

def enforce_request_policy(client, raw, timeout):
    policy = getattr(client, 'api_policy', None)
    if not isinstance(policy, ProjectAPIPolicy):
        fail('EGRESS_PROFILE_REQUIRED_USE_M4E43')
    try:
        return policy.before_send(client, raw, timeout)
    except ProviderError as error:
        if error.code != 'AUTOMATIC_API_DISABLED':
            policy.halt(error.code)
        raise


def complete_request_policy(client, reservation, attempt):
    if reservation is not None:
        client.api_policy.finish(reservation, attempt)


@contextmanager
def request_deadline(reservation, seconds):
    if reservation is None:
        yield
        return
    def expired(*_):
        raise TimeoutError('request wall deadline')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
