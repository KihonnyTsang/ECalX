"""Packaged BST computation with explicitly supplied, local reference data."""
import os
from pathlib import Path

from .bst_interpolate import BSTInterpolator


class BSTUnavailable(RuntimeError):
    """No usable reference dataset has been explicitly provided."""


def load_interpolator(csv_data=None):
    csv_path = os.environ.get('ECALX_BST_REFERENCE_CSV')
    if csv_data is None and not csv_path:
        raise BSTUnavailable('尚未提供参考数据，无法计算。请选择合法取得的204点参考CSV。')
    try:
        return BSTInterpolator(Path(csv_path).expanduser() if csv_path else None, csv_data=csv_data)
    except (OSError, ValueError, TypeError) as error:
        raise BSTUnavailable('参考数据读取或冻结校验失败，无法计算。请使用匹配的204点脱敏参考CSV。') from error
