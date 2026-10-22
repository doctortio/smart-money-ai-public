"""Non-publishing remote HMAC smoke test. Never calls reserve, mark or Telegram."""
import os
import time
import json
import uuid
import hmac
import hashlib
import requests

url = os.environ["TELEGRAM_LEDGER_GATEWAY_URL"]
secret = os.environ["TELEGRAM_LEDGER_HMAC_SECRET"]
base = {
    "operation": "config_get",
    "execution_key": "signal-center-config",
    "channel": "@smartmoneyaioficial",
    "method": "POST",
    "path": "/functions/v1/sma-telegram-ledger",
    "timestamp": int(time.time()),
    "nonce": str(uuid.uuid4()),
}
canonical = json.dumps(base, sort_keys=True, separators=(",", ":"))
body = dict(base, signature=hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest())
response = requests.post(url, json=body, timeout=20)
response.raise_for_status()
result = response.json()
assert result.get("ok") is True, "signed config_get rejected"
config = result.get("config")
assert isinstance(config, dict), "missing config object"
assert config.get("auto_enabled") is False, "automatic publishing unexpectedly enabled"
assert config.get("emergency_stop") is True, "emergency stop unexpectedly disabled"
print("PASS: signed config_get, automatic off, emergency stop on")

replay = requests.post(url, json=body, timeout=20)
assert replay.status_code == 409 and replay.json().get("error") == "replay", "nonce replay accepted"
print("PASS: nonce replay rejected")

bad = dict(base, nonce=str(uuid.uuid4()), signature="0" * 64)
denied = requests.post(url, json=bad, timeout=20)
assert denied.status_code == 401 and denied.json().get("error") == "invalid_signature", "invalid signature accepted"
print("PASS: invalid signature rejected")
