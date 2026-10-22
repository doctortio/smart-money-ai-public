"""Safety gate for later integrated homologation. No network, writes or Telegram."""
import pytest

HOMOLOG = "otqloermcjoposokoprg"

def check_readiness(project_ref, config, admin):
    if project_ref != HOMOLOG:
        raise PermissionError("homologation_project_required")
    if config.get("auto_enabled") is not False or config.get("emergency_stop") is not True:
        raise PermissionError("unsafe_signal_center_config")
    if not isinstance(admin, dict) or admin.get("tipo_usuario") != "ADMIN":
        raise PermissionError("admin_role_required")
    if not isinstance(admin.get("id"), int) or admin["id"] <= 0:
        raise PermissionError("admin_id_required")
    if not str(admin.get("email", "")).endswith("@example.invalid"):
        raise PermissionError("fictitious_admin_required")
    return True

def test_safe_homologation_passes():
    assert check_readiness(HOMOLOG, {"auto_enabled": False, "emergency_stop": True},
                           {"id": 1, "email": "test@example.invalid", "tipo_usuario": "ADMIN"})

@pytest.mark.parametrize("project,config,admin", [
    ("tigccvvuemverlbczpoh", {"auto_enabled": False, "emergency_stop": True}, {"id": 1, "email": "test@example.invalid", "tipo_usuario": "ADMIN"}),
    (HOMOLOG, {"auto_enabled": True, "emergency_stop": True}, {"id": 1, "email": "test@example.invalid", "tipo_usuario": "ADMIN"}),
    (HOMOLOG, {"auto_enabled": False, "emergency_stop": False}, {"id": 1, "email": "test@example.invalid", "tipo_usuario": "ADMIN"}),
    (HOMOLOG, {"auto_enabled": False, "emergency_stop": True}, {"id": 1, "email": "real@gmail.com", "tipo_usuario": "ADMIN"}),
    (HOMOLOG, {"auto_enabled": False, "emergency_stop": True}, {"id": 1, "email": "test@example.invalid", "tipo_usuario": "CLIENTE"}),
])
def test_unsafe_homologation_rejected(project, config, admin):
    with pytest.raises(PermissionError):
        check_readiness(project, config, admin)
