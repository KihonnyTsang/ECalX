import os
import sys
from pathlib import Path
sys.dont_write_bytecode=True
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
# Keep every test in disposable BYO state. Never load the owner's environment.
import pytest
@pytest.fixture(autouse=True)
def no_external_network(monkeypatch):
    import socket
    def blocked(*args, **kwargs):
        raise AssertionError('Release tests must not open a network connection')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket.socket, 'connect_ex', blocked)

@pytest.fixture(autouse=True)
def isolation(tmp_path,monkeypatch):
    from ecalx_local import configuration,byo_policy,engine,import_data,setup_ui
    root=tmp_path/'workspace'
    for mod in (configuration,byo_policy,engine,import_data,setup_ui):monkeypatch.setattr(mod,'SCIENCE_ROOT',root)
    monkeypatch.setenv('ECALX_CONFIG_DIR',str(tmp_path/'config'))
    monkeypatch.delenv('SILICONFLOW_API_KEY',raising=False)
    monkeypatch.delenv('SILICONFLOW_MODEL',raising=False)
    monkeypatch.setenv('ECALX_MODE','byo')
    return root
