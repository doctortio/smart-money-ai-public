"""Responsive presentation only; no signal or account decisions."""
from pathlib import Path


def apply_mobile_styles(st) -> None:
    css = Path(__file__).with_name("mobile.css").read_text(encoding="utf-8")
    st.markdown(f"<style>{css}</style>", unsafe_allow_html=True)
