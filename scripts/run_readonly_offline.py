"""Local-only read-only Streamlit entry; never loads credentials or live clients."""
import argparse
import ipaddress
import os
from pathlib import Path
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]


def loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8511)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--clean', action='store_true')
    modes.add_argument('--owner-review-preview', action='store_true')
    args = parser.parse_args()
    for name in list(os.environ):
        if any(s in name.upper() for s in ('API_KEY', 'ACCESS_TOKEN', 'REFRESH_TOKEN', 'SECRET_KEY')):
            os.environ.pop(name, None)
    os.environ.pop('ECALX_TEST_ONLY_PREVIEW_ROOT', None)
    os.environ['ECALX_READONLY_ROOT'] = str(ROOT)
    if args.clean:
        import tempfile
        os.environ['ECALX_READONLY_ROOT'] = tempfile.mkdtemp(prefix='ecalx-readonly-empty-')
    entry = 'readonly_demo.py'
    if args.owner_review_preview:
        import shutil
        import tempfile
        from ecalx.presentation.candidate_preview import load_candidate
        load_candidate(ROOT/'demo/public')
        isolated = Path(tempfile.mkdtemp(prefix='ecalx-owner-review-'))
        shutil.copytree(ROOT/'demo/public', isolated/'demo/public')
        os.environ['ECALX_TEST_ONLY_PREVIEW_ROOT'] = str(isolated)
        os.environ['ECALX_READONLY_ROOT'] = str(isolated)
        entry = 'test_only_preview.py'
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_dns = socket.getaddrinfo

    def check(address):
        if isinstance(address, tuple) and not loopback(address[0]):
            raise OSError('ECalX read-only: outbound network denied')

    def connect(sock, address):
        check(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        check(address)
        return original_connect_ex(sock, address)

    def dns(host, *rest, **kwargs):
        if host is not None and not loopback(host):
            raise OSError('ECalX read-only: external DNS denied')
        return original_dns(host, *rest, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = dns
    from streamlit.web import cli
    sys.argv = ['streamlit', 'run', str(ROOT/'app'/entry),
                '--server.address=127.0.0.1', '--server.port='+str(args.port),
                '--server.headless=true', '--browser.gatherUsageStats=false',
                '--server.fileWatcherType=none', '--client.toolbarMode=minimal']
    cli.main()


if __name__ == '__main__':
    main()
