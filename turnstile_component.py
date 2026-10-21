"""Componente Turnstile local para o cadastro público."""
from __future__ import annotations
from typing import Any
import streamlit as st

_TURNSTILE_JS = r'''
export default function(component) {
  const { parentElement, data, setStateValue } = component;
  const container = parentElement.querySelector('#sma-turnstile-container');
  const reset = () => setStateValue('token', '');
  const siteKey = data && data.site_key;
  if (!siteKey || !container) { reset(); return; }
  const render = () => {
    if (!window.turnstile || container.dataset.rendered === '1') { if (!window.turnstile) reset(); return; }
    container.dataset.rendered = '1';
    window.turnstile.render(container, {sitekey: siteKey, callback: (token) => setStateValue('token', token), 'expired-callback': reset, 'error-callback': reset, 'timeout-callback': reset});
  };
  let script = document.querySelector('script[data-sma-turnstile]');
  if (window.turnstile) render();
  else if (!script) {
    script = document.createElement('script'); script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'; script.async = true; script.defer = true; script.dataset.smaTurnstile = '1'; script.addEventListener('load', render, {once: true}); script.addEventListener('error', reset, {once: true}); document.head.appendChild(script);
  } else { script.addEventListener('load', render, {once: true}); script.addEventListener('error', reset, {once: true}); }
  return () => { if (window.turnstile && container) window.turnstile.remove(container); };
}
'''
try:
    _render_turnstile = st.components.v2.component('smart_money_turnstile_public_signup', html='<div id="sma-turnstile-container" style="min-height:65px"></div>', js=_TURNSTILE_JS, isolate_styles=False)
except Exception:
    _render_turnstile = None

def render_turnstile(site_key: str, *, key: str = 'public_signup_turnstile') -> Any:
    if _render_turnstile is None or not site_key:
        return None
    return _render_turnstile(data={'site_key': site_key}, key=key)
