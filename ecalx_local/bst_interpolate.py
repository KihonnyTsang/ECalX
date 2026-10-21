"""Packaged interpolation of a user-supplied, checksum-verified Dai BST dataset.

Numerical methods are unchanged from the frozen research prototype.
No training, uncertainty model, LGD calculation, network or ECalX imports.
"""
from __future__ import annotations

from bisect import bisect_left
import csv
from decimal import Decimal, InvalidOperation
import hashlib
from pathlib import Path

LABEL = '研究性文献插值'
DOI = '10.1111/ijac.13384'
ORIGINAL_SOURCE_SHA256 = 'b75cddb53985f24c8b825e3fbd732cb189e84570007b0bcfbc806a224d72badb'
SOURCE_SHA256 = '92a17a6e4f10487d747c7e316883fbab88c1cdc03e6efdec0e11d4a18851ec89'
REFERENCE_X = tuple(Decimal(x) for x in ['0.05', '0.15', '0.25', '0.35'])
FIELDS = tuple(Decimal(e) for e in ['10', '20', '30'])
KINDS = {
    'reference_digitized': '来源读图值（digitized）',
    'temperature_interpolation': '已有参考组成的温度线性插值',
    'composition_interpolation': '相邻参考组成线性插值',
}
PROTOCOL_NOTE = ('ΔE仅表示文献标称撤场幅值；实际E(t)、切换速度及完整波形协议未核实。'
                 'ΔT为正值降温幅值，不是经完整热损校正验证的带符号ΔTad。')


class UnsupportedInput(ValueError):
    """The requested composition, temperature or nominal field is unsupported."""


def number(value, name):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise UnsupportedInput(f'{name}必须是有限数值。') from None
    if not result.is_finite():
        raise UnsupportedInput(f'{name}必须是有限数值。')
    return result


def text_number(value):
    return format(value.normalize(), 'f')


class BSTInterpolator:
    """Piecewise linear T interpolation, then adjacent-composition interpolation."""

    def __init__(self, csv_path=None, *, csv_data=None):
        if csv_path is None and csv_data is None:
            raise ValueError('尚未提供BST参考数据，无法计算。')
        self.path = Path(csv_path) if csv_path is not None else None
        raw = csv_data if csv_data is not None else self.path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
            raise ValueError('参考CSV与冻结的204点版本不一致；没有计算数值。')
        self.curves = {}
        rows = list(csv.DictReader(raw.decode('utf-8-sig').splitlines()))
        if len(rows) != 204 or len({r['observation_id'] for r in rows}) != 204:
            raise ValueError('参考记录不完整。')
        for row in rows:
            if not (row['source_doi'] == DOI and row['data_origin'] == 'digitized'
                    and row['method'] == 'direct_thermocouple'
                    and row['target_definition'] == 'published_positive_cooling_magnitude_after_field_removal'):
                raise ValueError('参考来源、方法或温变定义不一致。')
            x = Decimal(row['sr_molar_fraction'])
            E = Decimal(row['field_kV_cm'])
            row = dict(row, x=x, E=E, T=Decimal(row['temperature_K']),
                       y=Decimal(row['delta_T_magnitude_K']))
            self.curves.setdefault((x, E), []).append(row)
        expected = {(x, E) for x in REFERENCE_X for E in FIELDS}
        if set(self.curves) != expected:
            raise ValueError('参考组成或电场不完整。')
        for curve in self.curves.values():
            curve.sort(key=lambda r: r['T'])
            if len(curve) != 17 or any(b['T'] <= a['T'] for a, b in zip(curve, curve[1:])):
                raise ValueError('参考曲线的17个温度点有缺失或重复。')

    def coverage(self, x, delta_e_kv_cm):
        """Return source anchors and their strict, closed temperature intersection."""
        x = number(x, 'Sr摩尔分数x')
        E = number(delta_e_kv_cm, '标称ΔE')
        if E not in FIELDS:
            raise UnsupportedInput('仅支持标称撤场幅值10、20、30 kV/cm；不插值或外推电场，也不把负值自动取绝对值。')
        if not REFERENCE_X[0] <= x <= REFERENCE_X[-1]:
            raise UnsupportedInput('Sr摩尔分数x仅支持[0.05, 0.35]；x是摩尔分数，不是百分数。不外推组成。')
        if x in REFERENCE_X:
            anchors = [(x, Decimal(1))]
        else:
            index = bisect_left(REFERENCE_X, x)
            lo, hi = REFERENCE_X[index-1], REFERENCE_X[index]
            weight = (x-lo)/(hi-lo)
            anchors = [(lo, 1-weight), (hi, weight)]
        Tmin = max(self.curves[(a, E)][0]['T'] for a, _ in anchors)
        Tmax = min(self.curves[(a, E)][-1]['T'] for a, _ in anchors)
        return x, E, anchors, Tmin, Tmax

    @staticmethod
    def source_record(row, temperature_weight):
        return dict(observation_id=row['observation_id'], data_origin='digitized',
                    temperature_K=float(row['T']), delta_T_magnitude_K=float(row['y']),
                    temperature_weight=float(temperature_weight), source_doi=DOI,
                    figure=row['figure'], pdf_page=int(row['pdf_page']),
                    curve_id=row['curve_id'], author_sample_label=row['author_sample_label'])

    def reference_value(self, x, E, T):
        curve = self.curves[(x, E)]
        temperatures = [r['T'] for r in curve]
        if not temperatures[0] <= T <= temperatures[-1]:
            raise UnsupportedInput('温度不在该参考曲线覆盖范围内；不进行端点外推。')
        index = bisect_left(temperatures, T)
        if temperatures[index] == T:
            row = curve[index]
            return row['y'], 'reference_digitized', [self.source_record(row, Decimal(1))]
        lo, hi = curve[index-1], curve[index]
        weight = (T-lo['T'])/(hi['T']-lo['T'])
        value = (1-weight)*lo['y']+weight*hi['y']
        records = [self.source_record(lo, 1-weight), self.source_record(hi, weight)]
        return value, 'temperature_interpolation', records

    def predict(self, x, temperature_K, delta_e_kv_cm):
        """Evaluate only within real coverage. Exact known observations take priority."""
        x, E, anchors, Tmin, Tmax = self.coverage(x, delta_e_kv_cm)
        T = number(temperature_K, '温度T（K）')
        if not Tmin <= T <= Tmax:
            raise UnsupportedInput(f'此组成/标称场的共同温度覆盖为[{Tmin}, {Tmax}] K；输入T={T} K超出覆盖，不输出ΔT。')
        total = Decimal(0)
        sources = []
        for a, composition_weight in anchors:
            value, kind, records = self.reference_value(a, E, T)
            total += composition_weight*value
            sources.append(dict(sr_molar_fraction=float(a), composition_weight=float(composition_weight),
                at_query_T_value_K=float(value), evaluation_kind=kind,
                figure=records[0]['figure'], source_records=records))
        kind = sources[0]['evaluation_kind'] if len(anchors) == 1 else 'composition_interpolation'
        return dict(label=LABEL, status='ok', result_kind=kind, result_description=KINDS[kind],
            composition=f'Ba{text_number(1-x)}Sr{text_number(x)}TiO3', sr_molar_fraction=float(x),
            temperature_K=float(T), nominal_delta_E_kV_cm=float(E),
            delta_T_magnitude_K=float(total), delta_T_unit='K',
            is_direct_reference_observation=kind == 'reference_digitized',
            target_definition='published_positive_cooling_magnitude_after_field_removal',
            temperature_coverage_K=[float(Tmin), float(Tmax)],
            calculation_method={'reference_digitized':'exact_digitized_reference_lookup',
                'temperature_interpolation':'piecewise_linear_temperature_at_known_composition',
                'composition_interpolation':'piecewise_linear_temperature_then_adjacent_composition_linear'}[kind],
            reference_sources=sources, source_doi=DOI, source_url='https://doi.org/'+DOI,
            source_csv_sha256=SOURCE_SHA256, source_data_origin='digitized;not_raw_experimental_arrays',
            protocol_note=PROTOCOL_NOTE,
            numerical_precision_note='插值计算的小数位不表示实验测量精度；不提供统计置信区间或实际误差承诺。')

    def curve(self, x, delta_e_kv_cm, include_temperature=None):
        """Union of source knots inside the strict intersection; no endpoint extension."""
        x, E, anchors, Tmin, Tmax = self.coverage(x, delta_e_kv_cm)
        grid = {r['T'] for a, _ in anchors for r in self.curves[(a, E)] if Tmin <= r['T'] <= Tmax}
        grid.update([Tmin, Tmax])
        if include_temperature is not None:
            T = number(include_temperature, '温度T（K）')
            if not Tmin <= T <= Tmax:
                raise UnsupportedInput('查询温度超出共同覆盖；不生成超范围曲线。')
            grid.add(T)
        return [self.predict(x, T, E) for T in sorted(grid)]

    def reference_rows(self, x, delta_e_kv_cm):
        _, E, anchors, _, _ = self.coverage(x, delta_e_kv_cm)
        return [dict(label=LABEL, result_kind='reference_digitized', sr_molar_fraction=float(a),
            temperature_K=float(r['T']), nominal_delta_E_kV_cm=float(E), delta_T_magnitude_K=float(r['y']),
            observation_id=r['observation_id'], data_origin='digitized', source_doi=DOI,
            figure=r['figure'], pdf_page=int(r['pdf_page'])) for a, _ in anchors for r in self.curves[(a, E)]]
