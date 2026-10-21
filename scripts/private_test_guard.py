"""Fail-closed guard for the optional private Telegram test.

This script never sends a message. It only validates that a future private
test was explicitly selected and cannot target the public channel.
"""
from __future__ import annotations

import os
import re
import sys


def main() -> int:
    dry_run = os.getenv("TELEGRAM_DRY_RUN", "true").strip().lower() == "true"
    chat_id = os.getenv("TELEGRAM_PRIVATE_CHAT_ID", "").strip()
    confirmation = os.getenv("PRIVATE_TEST_CONFIRMATION", "").strip()

    if dry_run:
        print("PRIVATE_TEST_GUARD=DRY_RUN")
        return 0
    if confirmation != "PRIVATE_TEST_AUTHORIZED":
        print("PRIVATE_TEST_GUARD=BLOCKED_CONFIRMATION", file=sys.stderr)
        return 2
    if chat_id.lower() in {"@smartmoneyaioficial", "smartmoneyaioficial"}:
        print("PRIVATE_TEST_GUARD=BLOCKED_PUBLIC_CHANNEL", file=sys.stderr)
        return 3
    if not re.fullmatch(r"-?\d+", chat_id):
        print("PRIVATE_TEST_GUARD=BLOCKED_CHAT_ID", file=sys.stderr)
        return 4
    print("PRIVATE_TEST_GUARD=PRIVATE_DESTINATION_ACCEPTED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
