"""Explicit BYO/personal dependency selection; never discover private workspaces."""
import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
MODE = os.environ.get('ECALX_MODE', 'byo')
if MODE not in {'byo', 'personal'}:
    raise ValueError('ECALX_MODE 必须为 byo 或 personal')
PERSONAL = MODE == 'personal'
if PERSONAL:
    if not os.environ.get('ECALX_SCIENCE_ROOT'):
        raise ValueError('个人模式请显式指定 ECALX_SCIENCE_ROOT 和 ECALX_LOCAL_STORAGE')
    if not os.environ.get('ECALX_LOCAL_STORAGE'):
        raise ValueError('个人模式需要显式指定原任务目录 ECALX_LOCAL_STORAGE')
    SCIENCE_ROOT = Path(os.environ['ECALX_SCIENCE_ROOT']).expanduser().resolve()
    sys.path.insert(0, str(SCIENCE_ROOT/'src'))
else:
    SCIENCE_ROOT = Path(os.environ.get('ECALX_WORKSPACE', str(Path.home()/'.local/share/ecalx'))).expanduser().resolve()
    # Editable source checkouts; installed wheels resolve ecalx normally.
    if (APP_ROOT/'src/ecalx').is_dir():
        sys.path.insert(0, str(APP_ROOT/'src'))
STORAGE = Path(os.environ.get('ECALX_LOCAL_STORAGE', str(SCIENCE_ROOT/'tasks'))).expanduser().resolve()
