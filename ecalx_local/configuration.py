"""Private local credentials and price-independent usage limits. No provider calls."""
import json
import os
import re
import stat
from pathlib import Path
from . import SCIENCE_ROOT
from ecalx.agent.config import BASE_URL, MODEL
from ecalx.agent.llm import ProviderError
from ecalx.agent.siliconflow import LLMBudget

# Conservative defaults; bounded adjustments reuse the existing runtime limits.
LIMITS = {
    'daily_requests': (20, 1, 200), 'daily_tokens': (262144, 1024, 2000000),
    'task_requests': (10, 1, 48), 'task_tokens': (131072, 1024, 1000000),
    'task_tool_calls': (16, 1, 64), 'output_tokens': (1024, 64, 4096),
    'request_seconds': (60, 1, 180), 'task_seconds': (180, 1, 600),
}
DEFAULT_LIMITS = {key: spec[0] for key, spec in LIMITS.items()}

SCOPE = ('研究问题、人工确认的条件、工具定义；最多8条检索记录的组成/温度/场路径/方法/来源摘要，'
         '最多4个候选及其种子来源和独立化学检查。追问仅发送所选已保存证据块、当前问题及最近6轮问题。'
         '不发送数据库原件、原始上传文件、raw_metadata、处理工艺自由文本或模型文件。'
         '请勿在研究问题、来源字段或追问中填写密钥和不希望外发的内容。')

def config_dir():
    return Path(os.environ.get('ECALX_CONFIG_DIR', str(Path.home()/'.config/ecalx'))).expanduser().absolute()

def private_dir():
    directory = config_dir()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid() or directory.stat().st_mode & 0o077:
        raise ValueError('配置目录必须由当前用户拥有且权限为700')
    return directory

def validate_config(value):
    model = value.get('model', MODEL)
    if not re.fullmatch(r'deepseek-ai/DeepSeek-[A-Za-z0-9._-]+', model):
        raise ValueError('请填写 SiliconFlow 的 DeepSeek 模型 ID，例如 deepseek-ai/DeepSeek-V4-Flash')
    limits = value.get('usage_limits', {})
    if not isinstance(limits, dict) or set(limits) - set(LIMITS):
        raise ValueError('用量限额字段无效')
    normalized = {}
    for key, (default, low, high) in LIMITS.items():
        amount = limits.get(key, default)
        if type(amount) is not int or not low <= amount <= high:
            raise ValueError('用量限额无效：' + key)
        normalized[key] = amount
    # Legacy pricing is retained as local metadata, never required or trusted
    # for admission or displayed as a current supplier quote.
    legacy = {key: value[key] for key in ('prices', 'daily_cny') if key in value}
    return {'model':model, 'usage_limits':normalized, **legacy}


def configured_budget(*, followup=False, cfg=None):
    limits = (cfg or read_config())['usage_limits']
    return LLMBudget(llm_requests=min(limits['task_requests'], 2) if followup else limits['task_requests'],
        tool_calls=min(limits['task_tool_calls'], 2) if followup else limits['task_tool_calls'],
        request_tokens=limits['output_tokens'],
        total_tokens=min(limits['task_tokens'], 65536) if followup else limits['task_tokens'],
        request_seconds=limits['request_seconds'],
        seconds=min(limits['task_seconds'], 150) if followup else limits['task_seconds'],
        transport_retries=0)

def read_config():
    directory = config_dir()
    if not directory.exists():
        raise ValueError('尚未配置在线模型和预算；离线科研工具仍可使用')
    private_dir()
    path = directory/'settings.json'
    fd = os.open(path, os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(fd) as f:
        info=os.fstat(f.fileno())
        if info.st_mode & 0o077 or not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid():
            raise ValueError('配置文件权限必须为600')
        return validate_config(json.load(f))

def atomic_private(name, text):
    directory=private_dir(); path=directory/name; temp=directory/(name+'.'+__import__('uuid').uuid4().hex+'.tmp')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(fd,'w') as f:f.write(text)
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)

def save_config(value, key=''):
    # Updating limits/model without entering a key leaves the private .env intact.
    value = dict(value)
    model = value.get('model', '')
    if not isinstance(model, str):
        raise ValueError('请填写有效的模型 ID')
    if model.strip():
        value['model'] = model.strip()
    else:
        value.pop('model', None)
    if (config_dir()/'settings.json').exists():
        value = {**read_config(), **value}
    elif 'model' not in value:
        raise ValueError('首次配置请填写模型 ID')
    value=validate_config(value)
    if key and (not re.fullmatch(r'[A-Za-z0-9_.-]{8,4096}',key)):
        raise ValueError('API Key 格式无效；不要包含空白或引号')
    atomic_private('settings.json',json.dumps(value,ensure_ascii=False,indent=2))
    if key:
        atomic_private('.env', 'SILICONFLOW_API_KEY='+key+'\n')

def load_credentials():
    from ecalx.agent.config import load_local_env
    cfg=read_config()
    status=load_local_env(config_dir())
    os.environ['SILICONFLOW_MODEL']=cfg['model']
    os.environ['SILICONFLOW_BASE_URL']=BASE_URL
    from ecalx.agent.config import siliconflow_settings
    siliconflow_settings()
    return status

def credential_configured():
    try:
        read_config()
        if os.environ.get('SILICONFLOW_API_KEY'):
            return True
        p=config_dir()/'.env'
        return p.is_file() and not p.is_symlink() and not p.stat().st_mode & 0o077
    except (ValueError,OSError):
        return False

def quote(*, followup=False, check_usage=True):
    cfg=read_config()
    if not credential_configured():
        raise ProviderError('未配置受保护的本地 API Key；可使用离线工具',code='MISSING_CREDENTIAL')
    budget = configured_budget(followup=followup, cfg=cfg)
    limits = cfg['usage_limits']
    from .byo_policy import usage_status
    status=usage_status()
    if check_usage:
        if status['unknown']:
            raise ProviderError('存在未知供应商用量；未发请求', code='UNKNOWN_USAGE')
        if (status['requests'] + budget.llm_requests > limits['daily_requests']
                or status['tokens'] + budget.total_tokens > limits['daily_tokens']):
            raise ProviderError('每日用量不足以容纳本任务上限；未发请求', code='BUDGET_LIMIT')
    # Consent contains stable limits, not a moving balance. Every actual send
    # still performs transactional daily/task accounting in BYOPolicy.
    return {'expected_requests':f'最多{budget.llm_requests}次：读取证据、引用证据回答' if followup else '通常约7–10次；空证据可提前结束',
        'max_requests':budget.llm_requests, 'max_tokens':budget.total_tokens,
        'max_tool_calls':budget.tool_calls, 'output_tokens':budget.request_tokens,
        'request_seconds':budget.request_seconds,'task_seconds':budget.seconds,
        'model':cfg['model'], 'daily_requests':limits['daily_requests'], 'daily_tokens':limits['daily_tokens'],
        'scope':SCOPE, 'notice':'SiliconFlow 在线请求可能产生费用；实际费用请以供应商账单为准。无自动重试。'}
