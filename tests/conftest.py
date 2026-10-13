"""Fail closed: the release tests may display saved values but cannot run science."""
import socket
import sqlite3
import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch, request, tmp_path):
    def denied(*a, **k):
        raise AssertionError('Release verification forbids network and database creation')
    for name in ('connect', 'connect_ex'):
        monkeypatch.setattr(socket.socket, name, denied)
    monkeypatch.setattr(socket, 'create_connection', denied)
    monkeypatch.setattr(socket, 'getaddrinfo', denied)
    if request.node.get_closest_marker('task_store'):
        original_connect = sqlite3.connect
        from pathlib import Path
        def temporary_only(database, *args, **kwargs):
            name = str(database)
            if name.startswith('file:'):
                name = name[5:].split('?', 1)[0]
            if not Path(name).resolve().is_relative_to(tmp_path.resolve()):
                raise AssertionError('Only pytest temporary SQLite stores are allowed')
            return original_connect(database, *args, **kwargs)
        monkeypatch.setattr(sqlite3, 'connect', temporary_only)
    else:
        monkeypatch.setattr(sqlite3, 'connect', denied)
    for key in ('SILICONFLOW_API_KEY', 'OPENAI_API_KEY', 'DEEPSEEK_API_KEY',
                'ECALX_READONLY_ROOT', 'ECALX_TEST_ONLY_PREVIEW_ROOT'):
        monkeypatch.delenv(key, raising=False)
