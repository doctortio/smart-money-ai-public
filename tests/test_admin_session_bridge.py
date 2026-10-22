import pytest
from telegram_worker.admin_session_bridge import resolve_admin_session

EMAIL = "admin@test.invalid"
ROW = {"id": 5, "email": EMAIL, "tipo_usuario": "ADMIN", "plano": "admin/base"}

def bridge(*, login=True, admin=True, rows=None):
    return resolve_admin_session(email=EMAIL, restore_login=lambda: login,
                                 is_admin=lambda email: admin,
                                 fetch_client=lambda email: [ROW] if rows is None else rows)

def test_verified_admin_id_comes_from_database():
    assert bridge()["admin_id"] == 5

@pytest.mark.parametrize("login,admin", [(False, True), (True, False)])
def test_invalid_session_rejected(login, admin):
    with pytest.raises(PermissionError):
        bridge(login=login, admin=admin)

def test_missing_identity_rejected():
    with pytest.raises(PermissionError):
        bridge(rows=[])

def test_duplicate_identity_rejected():
    with pytest.raises(PermissionError):
        bridge(rows=[ROW, ROW])

def test_nonadmin_database_role_rejected():
    with pytest.raises(PermissionError):
        bridge(rows=[dict(ROW, tipo_usuario="CLIENTE", plano="gratis")])

def test_wrong_email_rejected():
    with pytest.raises(PermissionError):
        bridge(rows=[dict(ROW, email="other@test.invalid")])

def test_missing_database_id_rejected():
    with pytest.raises(PermissionError):
        bridge(rows=[dict(ROW, id=None)])
