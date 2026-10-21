"""Fail-closed placeholder for future public publication activation."""
from __future__ import annotations

import sys


def main() -> int:
    print("PUBLIC_PUBLISH_BLOCKED=EXPLICIT_ACTIVATION_REQUIRED", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
