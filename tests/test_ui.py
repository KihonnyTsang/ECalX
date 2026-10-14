from pathlib import Path
from streamlit.testing.v1 import AppTest

def test_ui_initial_and_config(tmp_path,monkeypatch):
    import ecalx_local
    monkeypatch.setattr(ecalx_local,'STORAGE',tmp_path/'tasks')
    at=AppTest.from_file(str(Path(__file__).parents[1]/'app.py')).run(timeout=20)
    assert not at.exception
    assert any('检查研究条件'==b.label for b in at.button)
    at.radio(key='workspace').set_value('首次配置').run()
    assert not at.exception
    assert any('SiliconFlow API Key' in x.label for x in at.text_input)
    at.radio(key='workspace').set_value('导入数据').run()
    assert not at.exception
