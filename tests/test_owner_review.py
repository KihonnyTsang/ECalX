"""Actual frozen revision, owner scope, detached confirmation and real UI isolation."""
import hashlib
import json
from pathlib import Path
import shutil
import pytest
from streamlit.testing.v1 import AppTest
from ecalx.presentation.candidate_preview import load_candidate
from ecalx.presentation.replay import load_public, story_view
from ecalx.presentation.owner_review import approved_review
from helpers import make_packet, write_bundle

ROOT = Path(__file__).resolve().parents[1]
LATEST = '35bec3d5aac35463c3216ce6e613d6f4ae0539d51da61ef7ca1c844464088b0a'
OLD = '2005ae2de51c675dbc8e26e16de4c246e4fe039030483dccc23b2ecf6f5f0b98'


def test_real_latest_dossier_unsigned_and_bound():
    assert hashlib.sha256((ROOT/'demo/public/payload.json').read_bytes()).hexdigest() == LATEST
    packet = load_candidate(ROOT/'demo/public')
    assert len(packet['responses']) == 5
    assert len(packet['task']['calls']) == 5
    assert len(story_view(packet)['candidates']) == 2
    assert packet['public_review']['status'] == 'OWNER_REVIEW'
    assert packet['public_review']['payload_sha256'] == LATEST
    assert not (ROOT/'OWNER_APPROVAL.json').exists()
    assert load_public(ROOT) is None


def test_actual_preview_and_formal_b_remain_separate(tmp_path, monkeypatch):
    shutil.copytree(ROOT/'demo/public',tmp_path/'demo/public')
    monkeypatch.setenv('ECALX_TEST_ONLY_PREVIEW_ROOT',str(tmp_path))
    app = AppTest.from_file(str(ROOT/'app/test_only_preview.py'),default_timeout=60).run()
    assert not app.exception and not app.radio and not app.button and not app.metric
    assert any('OWNER_REVIEW' in x.value for x in app.error)
    assert any('五轮为预写模拟' in x.value and 'M5 禁用' in x.value for x in app.warning)
    assert any(len(x.value)==5 and '模拟轮次' in x.value.columns for x in app.dataframe)
    assert any('TEST_ONLY 模拟化学校验收据（未执行）' in x.value.columns for x in app.dataframe)
    app.selectbox[0].set_value(4).run()
    app.selectbox[1].set_value(1).run()
    assert not app.exception and any('CANDIDATE_02' in x.value for x in app.markdown)
    assert load_public(tmp_path) is None
    monkeypatch.setenv('ECALX_READONLY_ROOT',str(ROOT))
    formal=AppTest.from_file(str(ROOT/'app/readonly_demo.py'),default_timeout=60).run()
    formal.radio(key='replay_mode').set_value('B · 干净环境公开演示').run()
    assert not formal.exception and not formal.dataframe
    assert any('待所有者审核' in x.value for x in formal.info)


@pytest.mark.parametrize('field,value',[
    ('owner',''),('owner_approval',''),('review_record',''),('reviewed_at',''),
    ('reviewed_at','TEST_ONLY_NOT_A_DATE'),('reviewed_at','2026-10-13'),
    ('rights_review','PENDING'),('privacy_review','PENDING'),('status','OWNER_REVIEW'),
    ('status','APPROVED'),('payload_sha256',OLD),('release_manifest_sha256','0'*64),
    ('public_scope',[]),('public_scope',['demo/public/payload.json']),
    ('independent_reviewer','TEST_ONLY_FAKE_INDEPENDENT_REVIEWER'),
])
def test_owner_receipt_required_fields_and_latest_binding(tmp_path,field,value):
    receipt=write_bundle(tmp_path/'demo/public',make_packet(),approved_test_double=True)
    receipt[field]=value
    (tmp_path/'OWNER_APPROVAL.json').write_text(json.dumps(receipt))
    with pytest.raises(ValueError):load_public(tmp_path)


def test_self_review_is_allowed_without_independent_scientific_claim(tmp_path):
    write_bundle(tmp_path/'demo/public',make_packet(),approved_test_double=True)
    loaded=load_public(tmp_path)
    assert loaded['public_review']['owner']==loaded['public_review']['content_author']
    assert loaded['public_review']['status']=='OWNER_APPROVED'
    assert not loaded['public_review']['independent_reviewer']


@pytest.mark.parametrize('change',['extra','payload','review','manifest','link','scope_path'])
def test_any_final_release_change_or_scope_escape_blocks_approval(tmp_path,change):
    write_bundle(tmp_path/'demo/public',make_packet(),approved_test_double=True)
    if change=='extra':(tmp_path/'unexpected.txt').write_text('TEST_ONLY')
    elif change=='payload':
        p=tmp_path/'demo/public/payload.json';p.write_bytes(p.read_bytes()+b' ')
    elif change=='review':
        p=tmp_path/'demo/public/review.json';p.write_bytes(p.read_bytes()+b' ')
    elif change=='manifest':
        p=tmp_path/'audit/SHA256SUMS';p.write_bytes(p.read_bytes()+b'\n')
    elif change=='link':
        p=tmp_path/'demo/public/payload.json';p.rename(p.with_name('moved.json'));p.symlink_to(p.with_name('moved.json'))
    else:
        p=tmp_path/'demo/public/review.json';review=json.loads(p.read_bytes());review['public_scope'][0]['path']='../escape';p.write_text(json.dumps(review))
    with pytest.raises(ValueError):load_public(tmp_path)


def test_owner_preview_launcher_cannot_activate_publication(tmp_path,monkeypatch):
    import runpy,sys,os
    from streamlit.web import cli
    module=runpy.run_path(str(ROOT/'scripts/run_readonly_offline.py'),run_name='TEST_ONLY_IMPORT')
    monkeypatch.setattr(sys,'argv',['run_readonly_offline.py','--owner-review-preview'])
    def fake():
        isolated=Path(os.environ['ECALX_TEST_ONLY_PREVIEW_ROOT'])
        assert isolated!=ROOT and not (isolated/'OWNER_APPROVAL.json').exists()
        assert load_candidate(isolated/'demo/public')['local_preview'] is True
        assert load_public(isolated) is None
        assert any('test_only_preview.py' in arg for arg in sys.argv)
    monkeypatch.setattr(cli,'main',fake)
    module['main']()


def test_local_install_metadata_is_not_public_scope(tmp_path):
    write_bundle(tmp_path/'demo/public',make_packet(),approved_test_double=True)
    for name in ('.git','.venv','__pycache__','build','ecalx.egg-info'):
        directory=tmp_path/name;directory.mkdir();(directory/'TEST_ONLY_LOCAL_METADATA').write_text('TEST_ONLY')
    assert load_public(tmp_path)['public_review']['status']=='OWNER_APPROVED'
