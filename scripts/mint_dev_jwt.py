#!/usr/bin/env python
"""Mint an HS256 access token for local testing of the case workspace.

There is no unsigned mode for case routes (fail closed), so local calls need a
real token signed with the same secret the server has in CASE_JWT_SECRET
(or USER_MEMORY_JWT_SECRET). This signs one the way Node's user service does:
HS256, claim ``userId``, ``iat`` and ``exp``.

    python scripts/mint_dev_jwt.py --user-id 42 --ttl 3600
    curl -H "X-API-Key: $API_KEYS" -H "Authorization: Bearer $(python scripts/mint_dev_jwt.py --user-id 42)" \
         http://127.0.0.1:5000/pyapi/cases

Never use this against production.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.jwt_auth import mint_hs256  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--user-id", required=True, help="value for the userId claim (Node's numeric user key)")
    ap.add_argument("--secret", default=os.getenv("CASE_JWT_SECRET") or os.getenv("USER_MEMORY_JWT_SECRET") or "",
                    help="HS256 secret (default: CASE_JWT_SECRET, then USER_MEMORY_JWT_SECRET)")
    ap.add_argument("--claim", default=os.getenv("CASE_JWT_CLAIM", "userId"))
    ap.add_argument("--ttl", type=int, default=3600, help="seconds until expiry (default 3600)")
    args = ap.parse_args()
    if not args.secret:
        print("No secret: pass --secret or set CASE_JWT_SECRET", file=sys.stderr)
        return 2
    now = int(time.time())
    print(mint_hs256({args.claim: args.user_id, "iat": now, "exp": now + args.ttl}, args.secret))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
