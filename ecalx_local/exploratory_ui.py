"""Chinese, opt-in point estimation; sessions and receipts separate from tasks."""
import json
from datetime import datetime, timezone
from uuid import uuid4

import streamlit as st

from . import APP_ROOT, STORAGE, PERSONAL
from .ui import section_header, badge, science_note
from .exploratory import DEMOS, LIMITATIONS, MODELS, estimate, reason_text
from .protocol_ui import reference_controls, build_reference, show_protocol


def number(text, unit, temperature=False):
    if not text.strip():
        return None
    try:
        value = float(text)
    except ValueError:
        raise ValueError('请在数值框中只填写一个数值；温度范围请在研究条件页填写，评价点需另选。') from None
    import math
    if not math.isfinite(value):
        raise ValueError('请输入有限数值，不能使用 NaN 或无穷大。')
    if temperature:
        return value + 273.15 if unit == '°C' else value
    return value * {'kV/cm': 1, 'kV/mm': 10, 'MV/m': 10}[unit]


def range_plot(result):
    import altair as alt
    import pandas as pd
    contract = result['domain']['contract']
    for field, label in [('temperature_K', '评价温度 / K'), ('delta_E_kV_cm', '场变化幅度 ΔE / kV/cm')]:
        value = result['inputs'][field]
        if value is None:
            continue
        lo, hi = contract[field]
        frame = pd.DataFrame([{'下限': lo, '上限': hi, '评价点': value, '条件': label}])
        axis = alt.X('下限:Q', title=label, scale=alt.Scale(zero=False))
        band = alt.Chart(frame).mark_rule(strokeWidth=8, color='#a8d7d0').encode(x=axis, x2='上限:Q', y='条件:N')
        point = alt.Chart(frame).mark_point(size=110, filled=True, color='#147d78' if lo <= value <= hi else '#bd493b').encode(
            x=alt.X('评价点:Q', title=label), y=alt.Y('条件:N', title=None), tooltip=['下限:Q', '上限:Q', '评价点:Q'])
        st.altair_chart((band + point).properties(height=70), use_container_width=True)
    st.caption('横线仅表示训练条件范围，圆点是你选的评价点；范围内仍须通过组成、形态和近邻检查。这不是误差区间。')


def result_panel(result):
    show_protocol(result.get('field_protocol'))
    if result.get('parent_field_protocol'):
        show_protocol(result['parent_field_protocol'])
    status = result['status']
    if status == 'RESEARCH_ONLY':
        st.markdown('<div class="card-meta">'+badge('仅供探索','teal')+badge('性能待验证')+'</div>',unsafe_allow_html=True)
        st.metric('探索性 ΔT 估计（文献原报告标签）', f"{result['prediction_K']:.3f} K")
        inp = result['inputs']
        st.write(f"仅对应 **{inp['formula']}，{inp['temperature_K']:g} K，ΔE={inp['delta_E_kV_cm']:g} kV/cm**；"
                 + ('直接测量法' if inp['method'] == 'direct' else '间接推算法') + '，块体陶瓷，场路径未知。')
        st.warning('这是已有研究模型的统计估计，不是实测性能、温区保证或制冷温降承诺。正式性能仍需验证。')
    else:
        st.warning({'NEEDS_INPUT': '暂不估计：需要补充条件', 'REJECT': '当前条件不支持估计',
                    'UNAVAILABLE': '模型文件无法核验，暂不提供估计'}[status])
        for reason in result['reasons']:
            st.write('• ' + reason_text(reason))
    with st.expander('模型依据、训练条件与适用域'):
        metadata = result['metadata']
        if metadata:
            contract = result['domain']['contract']
            method = '直接法' if metadata['method'] == 'direct' else '间接法'
            st.write(f"模型：**{metadata['model']} / {method}** · 研究状态：TRAINED_UNVERIFIED")
            st.write('依据：模型用配方中各元素的原子比例、评价温度和 ΔE，学习已有文献数值之间的统计关系；没有计算实际晶体结构、制备工艺或热损。')
            counts = metadata['training_counts']
            st.caption(f"训练来源 A：{counts['rows']:,} 条记录、{counts['compositions']} 个名义组成、{counts['papers']} 篇论文；记录点不等于独立样品。")
            st.write(f"训练条件：{contract['temperature_K'][0]:g}–{contract['temperature_K'][1]:g} K；"
                     f"ΔE {contract['delta_E_kV_cm'][0]:g}–{contract['delta_E_kV_cm'][1]:g} kV/cm。")
            metrics = metadata['validation_metrics']
            mae, rmse = metrics.get('MAE_K'), metrics.get('RMSE_K')
            st.write('历史分组验证：' + (f"平均绝对误差 **{mae:.3f} K**，均方根误差 {rmse:.3f} K。" if mae is not None and rmse is not None else '独立组数不足，不报告误差。'))
            st.caption('误差属于该模型配套的历史验证，不是当前候选的误差界限；没有独立最终外测或校准区间。')
            decision = result['domain']['assessment']['decision']
            st.write('适用域结论：' + {'NOT_EVALUATED': '输入或研究条件未通过，尚未进入数值适用域检查。',
                'REJECT': '超出模型适用域。', 'INTERNAL_EXPLORATION_ONLY': '通过 M4-C 检查，仅限内部探索。'}[decision])
            range_plot(result)
    st.caption('估计仅供探索，候选仍需实验验证，不用于性能排名。')
    with st.expander('为什么只能探索？科学限制'):
        for line in LIMITATIONS:
            st.write('• ' + line)
    with st.expander('高级详情 · 版本哈希、适用域与离线收据'):
        st.json(result)
    st.download_button('下载本次探索性估计收据', json.dumps(result, ensure_ascii=False, indent=2),
                       result['receipt_id']+'.json', 'application/json', key='download_'+result['receipt_id'])


def estimator(prefix, *, formula=None, request=None, demo=False, parent_task_id=None, parent_protocol=None):
    input_area,result_area=st.columns([1.1,1],gap='large') if demo else (st.container(),st.container())
    with input_area:
        st.caption('可选、离线运行；点击估计才调用已有模型。只估计你明确选择的单个温度点。')
        if demo:
            choice = st.selectbox('演示情形', ['自由填写', *DEMOS], key=prefix+'_demo')
            if st.button('载入这组演示条件', key=prefix+'_load', disabled=choice == '自由填写'):
                values = DEMOS[choice]
                for key, value in {**values, 'model': 'RandomForest', 'tunit': 'K', 'funit': 'kV/cm',
                                   'e_start': values.get('e_start', ''), 'e_end': values.get('e_end', '')}.items():
                    st.session_state[prefix+'_'+key] = value
                st.session_state.pop(prefix+'_result', None)
            with st.expander('示例来源与使用说明'):
                st.write('示例为真实训练数据中的参考组成 BaTi0.89Sn0.11O3。主动载入示例不代表当前候选或新材料实测。缺评价点示例对应 333–353 K，评价点留空。')
                st.markdown('[示例论文 · 10.1002/adfm.202108182](https://doi.org/10.1002/adfm.202108182)')
                st.caption('记录：A-direct-88756a1392a3-10')
            formula = st.text_input('材料组成', key=prefix+'_formula')
        else:
            st.write('评价组成：' + formula)
        if request is not None:
            lo, hi = request.temperature_min_K, request.temperature_max_K
            if lo is not None or hi is not None:
                st.info(f"本研究温区：{'未指定' if lo is None else f'{lo:g}'} 至 {'未指定' if hi is None else f'{hi:g}'} K。请明确填写其中的评价点；单点不能代表整个温区。")
            if any(getattr(request, k) is not None for k in ('e_start_kV_cm', 'e_end_kV_cm', 'e_min_kV_cm', 'e_max_kV_cm')):
                st.warning('本研究已指定场路径或电场上下限，当前模型不支持。这里不会将它改为 ΔE，历史任务保持原样。')
        a, b = st.columns([2, 1])
        temp = a.text_input('评价温度点（必须明确选择）', key=prefix+'_temperature', placeholder='只填单个数值，不自动取中点')
        tunit = b.selectbox('评价温度单位', ['K', '°C'], key=prefix+'_tunit')
        a, b = st.columns([2, 1])
        de = a.text_input('ΔE 场变化幅度', key=prefix+'_delta_E', placeholder='选填；缺少时不估计')
        funit = b.selectbox('估计电场单位', ['kV/cm', 'kV/mm', 'MV/m'], key=prefix+'_funit')
        if parent_protocol:
            show_protocol(parent_protocol)
        reference = reference_controls(prefix+'_reference')
        path = st.selectbox('场条件的含义', ['仅 ΔE（路径未知）', '明确加载路径', '明确撤场路径', '其他明确路径 / 偏置'], key=prefix+'_path')
        st.caption('ΔE 不等于电场上限，也不默认 0→ΔE。填写起点、终点或选择明确路径，当前模型均拒答。')
        a, b = st.columns(2)
        start = a.text_input('场起点（可留空，不补 0）', key=prefix+'_e_start')
        end = b.text_input('场终点（可留空）', key=prefix+'_e_end')
        a, b = st.columns(2)
        method = a.selectbox('评价方法', ['direct', 'indirect'], index=None, placeholder='请选择方法',
            format_func=lambda x: {'direct': '直接测量法', 'indirect': '间接推算法'}[x], key=prefix+'_method')
        model = b.selectbox('探索模型', MODELS, index=None, placeholder='请选择已有模型', key=prefix+'_model')
        form = st.selectbox('评价样品形态', ['bulk ceramic', 'thin film', 'polymer', 'other'], index=None,
            placeholder='请选择形态，不推定实验事实', format_func=lambda x: {'bulk ceramic': '块体陶瓷', 'thin film': '薄膜', 'polymer': '聚合物', 'other': '其他'}[x], key=prefix+'_sample_form')
        raw = [formula, temp, tunit, de, funit, path, start, end, method, model, form, reference, parent_protocol,
               request.to_dict() if request is not None else None]
        fingerprint = json.dumps(raw, ensure_ascii=False, sort_keys=True)
        previous = st.session_state.get(prefix+'_result')
        if previous and previous['fingerprint'] != fingerprint:
            st.session_state.pop(prefix+'_result', None)
            st.caption('条件已更改，旧估计已清除；请重新运行。')
        if st.button('运行离线探索性估计', key=prefix+'_run', type='primary'):
            try:
                protocol = build_reference(reference)
                result = estimate(formula=formula, temperature_K=number(temp, tunit, True),
                    delta_E_kV_cm=number(de, funit), method=method, model=model, sample_form=form,
                    field_path={'仅 ΔE（路径未知）': 'unknown', '明确加载路径': 'loading', '明确撤场路径': 'unloading',
                                '其他明确路径 / 偏置': 'specified'}[path],
                    e_start=number(start, funit), e_end=number(end, funit), request=request,
                    protocol=protocol, parent_protocol=parent_protocol)
                result.update(receipt_id='exploratory-'+uuid4().hex, executed_at=datetime.now(timezone.utc).isoformat(),
                              execution='OFFLINE_EXISTING_MODEL_INFERENCE', demo=demo, paid_api_requests=0)
                if parent_task_id and request is not None:
                    from .conversation import fingerprint
                    from ecalx.agent.contracts import Goals
                    result.update(parent_task_id=parent_task_id,
                        parent_goals_sha256=fingerprint({k:v for k,v in request.to_dict().items() if k in Goals.model_fields}))
                destination = STORAGE.parent / 'exploratory'
                destination.mkdir(parents=True, exist_ok=True)
                (destination / (result['receipt_id']+'.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
                st.session_state[prefix+'_result'] = {'fingerprint': fingerprint, 'receipt': result}
            except ValueError as error:
                st.error(str(error))
    with result_area:
        if st.session_state.get(prefix+'_result'):
            result_panel(st.session_state[prefix+'_result']['receipt'])
        elif demo:
            with st.container(border=True):
                st.subheader('单点评价结果')
                st.caption('填好条件，主动点击运行后在这里查看。')
                st.write('需要明确组成、温度点、ΔE、测量方法、样品形态和已有模型。')
                science_note()
                st.caption('本地推断 · 无付费 API · 不修改研究任务')


def demo_page():
    if not PERSONAL:
        st.info("当前未配置可用的预测模型，暂不提供性能估计。")
        return
    section_header('离线模型探索', '探索一个明确的温度点', '选择材料与测试条件，查看已有模型可提供的探索性估计。')
    science_note()
    estimator('dt_demo',demo=True)
