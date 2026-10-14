from pathlib import Path
import json
from dataclasses import replace
import pytest
from streamlit.testing.v1 import AppTest
from ecalx.agent.contracts import Goals
from ecalx.data.repository import EvidenceRepository
from ecalx_local.import_data import FIELDS, preview, commit
from ecalx_local.evidence_matching import discover, amplitude, match_counts
from ecalx_local.conditions import EvidenceSnapshot, condition_summary, summary_lines
from ecalx_local.engine import LocalAgent, LocalWorkflow
from ecalx_local.jobs import create_research
from ecalx_local.inputs import prepare_inputs
from ecalx_local.presentation import report_markdown, evidence_rows

ROOT=Path(__file__).parents[1]
CSV=ROOT/'examples/SYNTHETIC_TEST_DATA_delta_labels.csv'


def data():
    raw=CSV.read_bytes()
    columns=raw.decode().splitlines()[0].split(',')
    p=preview(raw,CSV.name,{k:k for k in FIELDS if k in columns})
    assert not p['errors']
    return raw,p


def test_delta_query_retains_labels_without_endpoints():
    _,p=data();records=p['records'];before=json.dumps([r.to_dict() for r in records],sort_keys=True)
    out=discover(Goals(delta_E_kV_cm=20).request(),EvidenceSnapshot(records))
    assert len(out['evidence_only'])==5
    assert len(out['excluded'])==2  # upper bound and peak have no amplitude
    assert all(i['status']=='source_reported_data' for i in out['evidence_only'])
    assert out['match_counts']['delta_E_matches']==5
    assert out['match_counts']['reported_label_matches']==5
    assert out['match_counts']['delta_E_matches_path_unknown']==3
    assert out['match_counts']['verified_path_records']==0  # uploads don't self-verify
    assert all(r.e_start_kV_cm is None and r.e_end_kV_cm is None for r in records[:3])
    assert json.dumps([r.to_dict() for r in records],sort_keys=True)==before
    stats=out['descriptive_statistics']
    direct=next(s for s in stats if s['source']=='10.1234/synthetic-direct')
    indirect=next(s for s in stats if s['method']=='indirect')
    assert direct['record_count']==2 and direct['reported_delta_T_mean_K']==2
    assert indirect['reported_delta_T_mean_K']==-0.5
    assert indirect['delta_t_basis']=='maxwell_estimate' and direct['delta_t_basis']=='temperature_drop_magnitude'


@pytest.mark.parametrize('start,end,strict',[(0,20,1),(20,0,0)])
def test_specific_paths_get_auxiliary_evidence_only(start,end,strict):
    _,p=data();request=Goals(e_start_kV_cm=start,e_end_kV_cm=end).request()
    out=discover(request,EvidenceSnapshot(p['records']))
    scope=out['match_counts']
    assert scope['delta_E_matches']==5
    assert scope['amplitude_only_auxiliary_matches']==3
    auxiliary=[i for i in out['evidence_only'] if i['match_scope']=='amplitude_only_path_unconfirmed']
    assert len(auxiliary)==3 and all(i['unmet_constraints'] for i in auxiliary)
    assert all(i['evidence']['e_start_kV_cm'] is None for i in auxiliary)
    assert sum(i['match_scope']=='requested_conditions' for i in out['evidence_only'])==strict
    assert p['records'][2].delta_T_ad_K==-0.5  # unloading never flips the source label
    summary=condition_summary(p['records'],request,'test')
    assert summary['field_matches']==strict and summary['all_goal_matches']==strict
    if not strict:
        text='\n'.join(summary_lines(summary))
        assert '无严格路径匹配' in text and '3 条' in text
        assert '目标条件下无匹配实验依据' not in text


def test_bounds_peak_and_unknown_scalar_do_not_become_delta():
    _,p=data();cap,peak=p['records'][3:5]
    assert amplitude(cap) is None and amplitude(peak) is None
    assert amplitude(replace(cap,e_reported_kind='reported',e_reported_kV_cm=20)) is None
    req=Goals(e_max_kV_cm=20).request()
    assert not match_counts([cap,peak],req)['amplitude_only_auxiliary_matches']


def run_task(p,storage):
    t=create_research(p,'local',confirmed=True,storage=storage)
    a=LocalAgent(storage);a.advance(t['id'],LocalWorkflow());a.confirm_plan(t['id'],confirmed=True)
    done=a.run_until_wait(t['id'],LocalWorkflow())
    assert done['status']=='COMPLETED',done.get('error')
    return a,done


def test_reports_seed_coverage_and_ui_use_reported_data(isolation,tmp_path,monkeypatch):
    raw,p=data();commit(raw,p,isolation)
    before=[r.to_dict() for r in EvidenceRepository(isolation/'data/evidence.sqlite3').records()]
    a,done=run_task(prepare_inputs({'field_kind':'变化幅度 ΔE','field_value':'20'}),tmp_path/'tasks')
    search=next(c for c in done['calls'] if c['name']=='search_material_evidence')['output']['data']
    assert search['query_counts']['evidence_only']==5
    assert len(evidence_rows(done))==5
    generated=next(c for c in done['calls'] if c['name']=='generate_material_candidates')['output']['data']
    assert generated['seeds']
    assert all(s['coverage']['delta_E_matches']==5 for s in generated['seeds'])
    report=report_markdown(done)
    assert '原文报告 ΔT 的描述统计' in report
    assert '目标条件下无匹配实验依据' not in report
    assert '-0.5' in report and 'indirect' in report
    frozen=json.dumps(a.get(done['id']),sort_keys=True)
    import ecalx_local
    monkeypatch.setattr(ecalx_local,'STORAGE',tmp_path/'tasks')
    at=AppTest.from_file(str(ROOT/'app.py')).run(timeout=20)
    at.session_state.task_id=done['id'];at.run()
    assert not at.exception
    assert any(m.label=='ΔE幅度匹配记录' and m.value=='5' for m in at.metric)
    assert any('有效原文报告数据' in m.value for m in at.markdown)
    assert json.dumps(a.get(done['id']),sort_keys=True)==frozen
    assert [r.to_dict() for r in EvidenceRepository(isolation/'data/evidence.sqlite3').records()]==before
    assert not (isolation/'api_usage.sqlite3').exists()


def test_auxiliary_task_report_no_false_zero_evidence(isolation,tmp_path):
    raw,p=data();commit(raw,p,isolation)
    prepared=prepare_inputs({'field_kind':'起点 → 终点','e_start':'20','e_end':'0'})
    _,done=run_task(prepared,tmp_path/'tasks')
    report=report_markdown(done)
    assert '无严格路径匹配' in report and '幅度匹配、路径未确认' in report
    assert '目标条件下无匹配实验依据' not in report
    rows=evidence_rows(done)
    assert len(rows)==3 and all(r['match_scope']=='amplitude_only_path_unconfirmed' for r in rows)
