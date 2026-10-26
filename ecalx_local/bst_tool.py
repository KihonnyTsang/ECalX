"""Packaged BST computation with explicitly supplied, local reference data."""
import os
from pathlib import Path

from .bst_interpolate import BSTInterpolator


class BSTUnavailable(RuntimeError):
    """No usable reference dataset has been explicitly provided."""


def load_interpolator(csv_data=None, *, root=None):
    from .bst_reference import reference_path
    imported = reference_path(root)
    csv_path = imported if imported.is_file() else os.environ.get('ECALX_BST_REFERENCE_CSV')
    if csv_data is None and not csv_path:
        raise BSTUnavailable('尚未导入参考数据，请前往“设置 → 数据库构建 → 文献参考曲线”导入一次，后续研究可直接复用。')
    try:
        return BSTInterpolator(Path(csv_path).expanduser() if csv_path else None, csv_data=csv_data)
    except (OSError, ValueError, TypeError) as error:
        raise BSTUnavailable('参考数据读取或冻结校验失败，无法计算。请在“设置 → 数据库构建”检查匹配的204点参考CSV。') from error
