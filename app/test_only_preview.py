"""Separate local review surface; cannot select A or B or approve a bundle."""
import os
from pathlib import Path

import streamlit as st

from portfolio_demo import introduction, render_packet
from ecalx.presentation.candidate_preview import load_candidate

st.set_page_config(page_title='ECalX · TEST_ONLY 待审预览', page_icon='🔬', layout='wide')
introduction()
st.error('TEST_ONLY · 隔离本地待审预览 · OWNER_REVIEW / 历史待审 · 不是获准公开 B 模式')
root = os.environ.get('ECALX_TEST_ONLY_PREVIEW_ROOT')
if not root:
    st.info('使用 scripts/run_readonly_offline.py --test-only-preview 启动隔离副本。')
else:
    try:
        folder = Path(root)/'demo/public' if (Path(root)/'demo/public').is_dir() else Path(root)/'candidate'
        render_packet(load_candidate(folder))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        st.warning('TEST_ONLY 待审包缺失、损坏或状态不符。不会执行工具或自动批准。')
