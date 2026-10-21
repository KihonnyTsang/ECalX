from pathlib import Path
from streamlit.testing.v1 import AppTest

def test_ui_initial_and_config(tmp_path,monkeypatch):
    import ecalx_local
    monkeypatch.setattr(ecalx_local,'STORAGE',tmp_path/'tasks')
    at=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=20)
    assert not at.exception
    assert any('检查研究条件'==b.label for b in at.button)
    next(b for b in at.button if b.label=='设置').click().run()
    assert not at.exception
    assert [x.label for x in at.text_input]==['API Key','模型名称']
    assert all(x.value=='' for x in at.text_input)
    assert not any('单价' in x.label or '人民币' in x.label for x in at.text_input)
    assert not at.expander[0].proto.expanded
    from ecalx_local.configuration import read_config, config_dir
    at.text_input[0].set_value('TEST_ONLY_NOT_A_REAL_KEY')
    next(b for b in at.button if b.label=='仅保存本机配置').click().run()
    assert any('首次配置请填写模型 ID' in e.value for e in at.error)
    assert not (config_dir()/'settings.json').exists()
    assert not (config_dir()/'.env').exists()
    at.text_input[0].set_value('TEST_ONLY_NOT_A_REAL_KEY')
    at.text_input[1].set_value('invalid-model')
    next(b for b in at.button if b.label=='仅保存本机配置').click().run()
    assert at.error and not (config_dir()/'settings.json').exists()
    at.text_input[0].set_value('TEST_ONLY_NOT_A_REAL_KEY')
    at.text_input[1].set_value('deepseek-ai/DeepSeek-V4-Flash')
    next(b for b in at.button if b.label=='仅保存本机配置').click().run()
    assert not at.exception and any('已保存' in s.value for s in at.success)
    assert read_config()['model']=='deepseek-ai/DeepSeek-V4-Flash'
    key_file=config_dir()/'.env'
    before=key_file.read_bytes()
    saved=read_config()
    # Reopen the page: saved values must remain outside both empty inputs.
    next(b for b in at.button if b.label=='数据库构建').click().run()
    next(b for b in at.button if b.label=='API 配置').click().run()
    assert all(x.value=='' for x in at.text_input)
    assert any('当前已配置模型：deepseek-ai/DeepSeek-V4-Flash' in c.value for c in at.caption)
    at.text_input[0].set_value('')
    at.text_input[1].set_value('')
    next(b for b in at.button if b.label=='仅保存本机配置').click().run()
    assert key_file.read_bytes()==before and read_config()==saved
    at.text_input[1].set_value('deepseek-ai/DeepSeek-V3')
    next(b for b in at.button if b.label=='仅保存本机配置').click().run()
    assert not at.exception and any('已保存' in s.value for s in at.success)
    assert read_config()['model']=='deepseek-ai/DeepSeek-V3'
    assert read_config()['usage_limits']==saved['usage_limits']
    assert key_file.read_bytes()==before
    assert any('当前已配置模型：deepseek-ai/DeepSeek-V3' in c.value for c in at.caption)
    assert (config_dir()/'settings.json').stat().st_mode&0o777==0o600
    assert key_file.stat().st_mode&0o777==0o600
    next(b for b in at.button if b.label=='数据库构建').click().run()
    assert not at.exception


def test_byo_confirmation_shows_usage_and_supplier_notice(isolation, tmp_path, monkeypatch):
    import ecalx_local
    from test_http_contract import configured
    configured()
    monkeypatch.setattr(ecalx_local,'STORAGE',tmp_path/'tasks')
    from ecalx_local.engine import online_quote
    from ecalx_local.conversation_live import followup_quote
    # Exercise the shared renderer used by research, child research and follow-up.
    for q in (online_quote(), followup_quote()):
        app=AppTest.from_string('from ecalx_local.ui_home import show_quote\nshow_quote('+repr(q)+')').run()
        assert not app.exception
        text=' '.join(x.value for x in list(app.markdown)+list(app.caption))
        assert '实际费用请以供应商账单为准' in text and '可能产生费用' in text
        assert 'Token' in text and '工具调用' in text and '自动重试 0 次' in text
        assert '¥' not in text and '单价有效期' not in text
