"""Login-only homologation entrypoint. No production imports or Telegram calls."""
import os
import requests
import streamlit as st

st.set_page_config(page_title="Smart Money AI | Homologação", page_icon="🧪")
st.title("Smart Money AI — Homologação")
st.warning("Ambiente exclusivo para teste de autenticação. Sem sinais reais, pagamentos ou envios ao Telegram.")

def setting(key):
    return st.secrets.get(key, os.environ.get(key, ""))

url = setting("SMA_HOMOLOG_SUPABASE_URL").rstrip("/")
key = setting("SMA_HOMOLOG_SUPABASE_ANON_KEY")
expected_project = "otqloermcjoposokoprg"
if not url or not key or url != f"https://{expected_project}.supabase.co":
    st.error("Configuração de homologação ausente ou incorreta. Nenhuma conexão será feita.")
    st.stop()

if st.session_state.get("homolog_email"):
    st.success("Autenticação confirmada: " + st.session_state["homolog_email"])
    st.info("Este teste comprova apenas o login. A autorização ADMIN no aplicativo ainda requer integração e teste específicos.")
    if st.button("Sair"):
        st.session_state.pop("homolog_email", None)
        st.rerun()
else:
    with st.form("login_homolog"):
        email = st.text_input("E-mail")
        password = st.text_input("Senha", type="password")
        submit = st.form_submit_button("Entrar na homologação")
    if submit:
        if not email or not password:
            st.error("Informe e-mail e senha.")
        else:
            try:
                response = requests.post(
                    f"{url}/auth/v1/token?grant_type=password",
                    headers={"apikey": key, "Content-Type": "application/json"},
                    json={"email": email, "password": password},
                    timeout=12,
                )
                data = response.json()
                returned_email = (data.get("user") or {}).get("email", "")
                if response.ok and returned_email.lower() == "doctortio@gmail.com":
                    st.session_state["homolog_email"] = returned_email
                    st.rerun()
                else:
                    st.error("Login não autorizado neste teste ou credenciais inválidas.")
            except (requests.RequestException, ValueError):
                st.error("Não foi possível concluir a autenticação. Verifique a configuração e tente novamente.")
