"""Cache only the stateless UI agent handle, never task state or model outputs."""
import streamlit as st


@st.cache_resource(show_spinner=False)
def ui_agent(storage, science_root, personal):
    from .engine import LocalAgent
    return LocalAgent(storage)
