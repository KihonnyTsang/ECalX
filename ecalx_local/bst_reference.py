"""Private, reusable literature curves, separate from experimental evidence."""
import csv
import hashlib
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .bst_interpolate import BSTInterpolator, DOI, SOURCE_SHA256, ORIGINAL_SOURCE_SHA256

DATASET_ID = 'bst-dai-' + SOURCE_SHA256[:16]


def workspace_root(root=None):
    if root is not None:
        return Path(root)
    from . import PERSONAL, SCIENCE_ROOT, STORAGE
    return STORAGE.parent if PERSONAL else SCIENCE_ROOT


def dataset_dir(root=None):
    return workspace_root(root) / 'reference_datasets' / DATASET_ID


def reference_path(root=None):
    return dataset_dir(root) / 'reference.csv'


def reference_bytes(raw):
    """Keep frozen values verbatim; only strip private path metadata if needed."""
    if hashlib.sha256(raw).hexdigest() == ORIGINAL_SOURCE_SHA256:
        rows = list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
        fields = [key for key in rows[0] if key != 'source_pdf_path']
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows({key: row[key] for key in fields} for row in rows)
        raw = output.getvalue().encode('utf-8')
    BSTInterpolator(csv_data=raw)  # Original frozen checksum and source checks.
    return raw


def import_reference(raw, root=None):
    raw = reference_bytes(raw)
    model = BSTInterpolator(csv_data=raw)
    directory = dataset_dir(root)
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = reference_path(root)
    if target.exists():
        if target.read_bytes() != raw:
            raise ValueError('已导入参考数据的校验失败；未覆盖本机文件。')
        return metadata(root)
    manifest = {
        'dataset_id': DATASET_ID, 'dataset_kind': 'literature_reference_curves',
        'label': 'Dai · BST 文献参考曲线', 'source_doi': DOI,
        'data_origin': 'digitized', 'source_figures': ['8A', '8B', '8C', '8D'],
        'point_count': 204, 'paper_count': 1, 'curve_count': 12,
        'reference_compositions': [0.05, 0.15, 0.25, 0.35],
        'independent_experiment_count': None, 'experimentally_verified': False,
        'count_notice': '204个相关的文献数字化曲线点，不是204次独立实验；不并入实验数据库。',
        'source_csv_sha256': SOURCE_SHA256, 'frozen_original_sha256': ORIGINAL_SOURCE_SHA256,
        'applicability': {
            'material': 'Ba(1−x)Sr(x)TiO3', 'sample_form': 'bulk_ceramic',
            'sr_molar_fraction': [0.05, 0.35], 'nominal_delta_E_kV_cm': [10, 20, 30],
            'temperature': 'strict intersection of supporting curves; no extrapolation',
            'curves': [{'x': float(x), 'field_kV_cm': float(e),
                        'temperature_K': [float(rows[0]['T']), float(rows[-1]['T'])],
                        'figure': rows[0]['figure']}
                       for (x, e), rows in sorted(model.curves.items())],
        },
        'protocol_note': '文献标称撤场幅值；完整波形未核实。ΔT为正值降温幅值。',
        'imported_at': datetime.now(timezone.utc).isoformat(),
    }
    # No supplied filename, absolute source path, PDF or image is copied here.
    with tempfile.TemporaryDirectory(prefix='.import-', dir=directory.parent) as staging:
        for name, content in (('reference.csv', raw), ('manifest.json',
                              json.dumps(manifest, ensure_ascii=False, indent=2).encode())):
            fd = os.open(Path(staging)/name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as output:
                output.write(content)
        try:
            os.rename(staging, directory)
        except FileExistsError:
            if target.read_bytes() != raw:
                raise ValueError('已存在的参考版本不同；未覆盖。') from None
            return metadata(root)
    return manifest


def metadata(root=None):
    path = dataset_dir(root) / 'manifest.json'
    return json.loads(path.read_text()) if path.is_file() else None
