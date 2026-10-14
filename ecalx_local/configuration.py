"""Per-user credentials and explicit user-priced budgets. No provider calls."""
import json
import os
import re
import stat
from pathlib import Path
from . import SCIENCE_ROOT
from ecalx.agent.config import BASE_URL, MODEL
from ecalx.agent.llm import ProviderError
from ecalx.agent.siliconflow import TokenPrices

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
    prices = TokenPrices.model_validate(value.get('prices', {})).model_dump()
    if any(prices[k] <= 0 for k in ('input_per_million','output_per_million')):
        raise ValueError('请按供应商当前价格填写正数单价；不会使用默认历史价格')
    cap = value.get('daily_cny')
    if type(cap) not in (int,float) or not 0 < cap <= 1000:
        raise ValueError('每日本地估算预算须在0–1000元之间（不含0）')
    return {'model':model, 'prices':prices, 'daily_cny':float(cap)}

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

def quote():
    from .engine import BUDGET
    cfg=read_config()
    if not credential_configured():
        raise ProviderError('未配置受保护的本地 API Key；可使用离线工具',code='MISSING_CREDENTIAL')
    rate=max(v for v in cfg['prices'].values() if v is not None)
    from .byo_policy import usage_status
    status=usage_status()
    worst=round(BUDGET.total_tokens*rate/1_000_000,6)
    if status['unknown'] or status['cost']+worst > cfg['daily_cny']:
        raise ProviderError('本地预算不足或存在未知用量；未发请求',code='BUDGET_LIMIT')
    return {'expected_requests':'约7–10次；空证据可提前结束', 'max_requests':BUDGET.llm_requests,
        'max_tokens':BUDGET.total_tokens,'max_estimated_cny':worst,
        'request_seconds':BUDGET.request_seconds,'task_seconds':BUDGET.seconds,
        'prices':cfg['prices'], 'model':cfg['model'],'daily_cny':cfg['daily_cny'],
        'rate_valid_through':'用户本机配置，变更后须重新确认',
        'scope':SCOPE, 'notice':'按你填写的单价保守估算；本地预算不是供应商账单硬上限。无自动重试。'}
