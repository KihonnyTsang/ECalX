import os
import sys
from pathlib import Path

def main():
    from streamlit.web import cli
    os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
    sys.argv = ['streamlit', 'run', str(Path(__file__).with_name('app.py')),
                '--server.address=127.0.0.1', '--browser.gatherUsageStats=false',
                '--client.toolbarMode=minimal', '--theme.base=light',
                '--theme.primaryColor=#117C78','--theme.backgroundColor=#FCFCFA',
                '--theme.secondaryBackgroundColor=#F1F4F4','--theme.textColor=#193248',
                '--theme.font=sans-serif',*sys.argv[1:]]
    cli.main()
