from pathlib import Path


APP = Path(__file__).resolve().parents[2] / "app.py"
SOURCE = APP.read_text(encoding="utf-8")


def test_public_signup_uses_server_side_validation_and_client_profile():
    block = SOURCE[SOURCE.index("def validar_cadastro_publico"):SOURCE.index("def tela_login")]
    assert "validar_senha_nova" in block
    assert "PUBLIC_SIGNUP_FUNCTION_URL" in block
    migration = (APP.parent / "supabase" / "migrations" / "20261015000000_public_signup_rate_limit.sql").read_text(encoding="utf-8")
    assert "'gratis'" in migration


def test_public_signup_validation_contract_is_explicit():
    block = SOURCE[SOURCE.index("def validar_cadastro_publico"):SOURCE.index("def pagina_comercial_publica")]
    assert "validar_senha_nova" in block
    assert "@" in block
    assert "As senhas não conferem" in block


def test_public_signup_does_not_accept_plan_or_credit_values_from_form():
    block = SOURCE[SOURCE.index("def pagina_comercial_publica"):SOURCE.index("def tela_login")]
    assert "st.number_input" not in block
    assert "saldo_creditos" not in block
    assert "service_role" not in block


def test_public_signup_uses_transactional_backend_rpc_and_generic_errors():
    block = SOURCE[SOURCE.index("def criar_cadastro_publico_backend"):SOURCE.index("def pagina_comercial_publica")]
    assert "PUBLIC_SIGNUP_FUNCTION_URL" in block
    assert "REQUEST_ACCEPTED" not in block


def test_edge_function_uses_email_limit_and_hides_service_role():
    edge = (APP.parent / "supabase" / "functions" / "sma-public-signup" / "index.ts").read_text(encoding="utf-8")
    assert "p_senha_hash: hash" in edge
    assert "p_ip_hash" not in edge
    assert "cf-connecting-ip" not in edge
    assert "x-forwarded-for" not in edge.lower()
    assert "SUPABASE_SERVICE_ROLE_KEY" in edge
    assert 'code: ok ? "CREATED" : "REQUEST_ACCEPTED"' in edge
    assert "validateCaptcha" in edge
    assert "p_token_hash: tokenHash" in edge
    assert "EMAIL_PROVIDER_API_KEY" in edge


def test_scrypt_parameters_match_python_login_format():
    edge = (APP.parent / "supabase" / "functions" / "sma-public-signup" / "index.ts").read_text(encoding="utf-8")
    migration = (APP.parent / "supabase" / "migrations" / "20261015000000_public_signup_rate_limit.sql").read_text(encoding="utf-8")
    assert "scrypt\\$131072\\$8\\$1" in edge
    assert "scrypt-js" not in edge
    assert "p_senha_hash !~ '^scrypt" in migration
    assert "hashlib.scrypt" in SOURCE


def test_commercial_entrypoint_is_internal_and_production_url_is_not_github_pages():
    assert 'st.button("🚀 ASSINAR AGORA"' in SOURCE
    assert "doctortio.github.io/smartmoneyai-site" not in SOURCE
    assert "smart-money-ai-4.streamlit.app" in SOURCE
