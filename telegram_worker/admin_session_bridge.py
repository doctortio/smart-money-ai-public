"""Server-side identity bridge. Does not trust browser-provided role or user id."""
from __future__ import annotations

def resolve_admin_session(*, email, restore_login, is_admin, fetch_client):
    if not isinstance(email, str) or not email.strip():
        raise PermissionError("admin_session_required")
    if not restore_login() or not is_admin(email):
        raise PermissionError("admin_session_required")
    rows = fetch_client(email.strip().lower())
    if not isinstance(rows, list) or len(rows) != 1:
        raise PermissionError("admin_identity_unavailable")
    row = rows[0]
    if str(row.get("email", "")).strip().lower() != email.strip().lower():
        raise PermissionError("admin_identity_mismatch")
    if str(row.get("tipo_usuario", "")).upper() != "ADMIN" and str(row.get("plano", "")).lower() != "admin/base":
        raise PermissionError("admin_role_not_in_database")
    identifier = row.get("id")
    if type(identifier) is not int or identifier <= 0:
        raise PermissionError("admin_identity_missing_id")
    return {"authenticated": True, "is_admin": True, "admin_id": identifier, "email": row["email"]}
