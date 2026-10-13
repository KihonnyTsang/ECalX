"""Read-only entry: no API key loading, provider, task store or database creation."""
import os
from pathlib import Path

import streamlit as st

from portfolio_demo import render

st.set_page_config(page_title='ECalX · Agent 只读演示', page_icon='🔬', layout='wide')
# Optional isolated artifact root supports clean-room verification without moving originals.
render(Path(os.environ.get('ECALX_READONLY_ROOT', Path(__file__).resolve().parents[1])))
