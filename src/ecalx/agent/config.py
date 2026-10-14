"""Server-only, allowlisted local settings. Never expose environment values wholesale."""
import os
import shlex
import stat
from pathlib import Path

from .llm import ProviderError

MODEL = 'deepseek-ai/DeepSeek-V4-Flash'
BASE_URL = 'https://api.siliconflow.cn/v1'
ENV_NAMES = frozenset({'SILICONFLOW_API_KEY', 'SILICONFLOW_MODEL',
                       'SILICONFLOW_BASE_URL', 'ECALX_LLM_PROVIDER'})


def load_local_env(root):
    """Read only root/.env; no interpolation, shell execution, discovery or overrides.

    Return status only, never parsed values. Existing environment (even empty) wins.
    Parse atomically so an invalid file cannot partially change the environment.
    """
    path = Path(root) / '.env'
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return {'status': 'ENV_FILE_MISSING', 'message': '.env缺失；可使用进程环境，离线功能正常'}
    except OSError:
        return {'status': 'ENV_FILE_REJECTED', 'message': '.env不可读取或为符号链接；离线功能正常'}
    try:
        with os.fdopen(fd, 'r', encoding='utf-8') as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                return {'status': 'ENV_FILE_REJECTED', 'message': '.env须为当前用户拥有的私有普通文件（权限600）'}
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError
        values = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[7:].strip()
            name, sep, value = line.partition('=')
            name = name.strip()
            if name not in ENV_NAMES:
                continue
            if not sep or name in values:
                raise ValueError
            parts = shlex.split(value, comments=True, posix=True)
            if len(parts) > 1:
                raise ValueError
            values[name] = parts[0] if parts else ''
        for name, value in values.items():
            os.environ.setdefault(name, value)
        return {'status': 'ENV_FILE_LOADED', 'message': '本地配置已安全加载；显式环境变量优先'}
    except (ValueError, UnicodeError, OSError):
        return {'status': 'ENV_FILE_REJECTED', 'message': '.env格式无效；未加载文件内配置，离线功能正常'}


def siliconflow_settings():
    model = os.environ.get('SILICONFLOW_MODEL', MODEL)
    base = os.environ.get('SILICONFLOW_BASE_URL', BASE_URL).rstrip('/')
    # Stage-specific allowlist: a typo or alternate host must never receive the key.
    if (os.environ.get('ECALX_MODE', 'byo') == 'personal' and model != MODEL) or (os.environ.get('ECALX_MODE', 'byo') != 'personal' and not __import__('re').fullmatch(r'deepseek-ai/DeepSeek-[A-Za-z0-9._-]+', model)):
        raise ProviderError('SILICONFLOW_MODEL不在本阶段允许的模型列表', code='INVALID_CONFIG')
    if base != BASE_URL:
        raise ProviderError('SILICONFLOW_BASE_URL必须为官方HTTPS /v1地址', code='INVALID_CONFIG')
    provider = os.environ.get('ECALX_LLM_PROVIDER', 'auto')
    if provider not in {'auto', 'siliconflow', 'openai', 'deterministic'}:
        raise ProviderError('ECALX_LLM_PROVIDER配置无效', code='INVALID_CONFIG')
    key = os.environ.get('SILICONFLOW_API_KEY', '')
    if not key or len(key) > 4096 or any(c.isspace() or ord(c) < 32 for c in key):
        raise ProviderError('需要服务端SILICONFLOW_API_KEY；.env缺失时可使用离线工作流', code='MISSING_CREDENTIAL')
    return model, base


def connection_status(root=None):
    """No network/charge. Configured is distinct from authenticated/tool validated."""
    local = load_local_env(root) if root is not None else None
    try:
        model, base = siliconflow_settings()
        result = {'provider': 'siliconflow', 'model': model, 'base_url': base,
                  'status': 'CONFIGURED_NOT_VERIFIED', 'message': '配置就绪；尚未检测联网/真实工具往返'}
    except ProviderError as error:
        result = {'provider': 'siliconflow', 'status': error.code, 'message': str(error)}
    if local:
        result['local_config'] = local
    return result
