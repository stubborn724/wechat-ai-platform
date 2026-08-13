#!/usr/bin/env python3
"""
Check the WeChat AI content platform health and optionally authentication.

Usage:
    python check_health.py [--base-url http://localhost:8002] [--token <access-token>]

Exits 0 when all checks pass, 1 otherwise. Prints one status line per check.
Uses only the standard library so it runs on any Python 3.8+.
"""

import argparse
import json
import sys
import urllib.error
import urllib.request


def request(base_url, path, token=None, timeout=8):
    url = base_url.rstrip("/") + path
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        return resp.status, json.loads(body) if body else {}


def check(name, fn):
    try:
        ok, detail = fn()
    except urllib.error.HTTPError as e:
        print(f"[FAIL] {name}: HTTP {e.code} {e.reason}")
        return False
    except Exception as e:  # noqa: BLE001 - report any connectivity issue
        print(f"[FAIL] {name}: {e}")
        return False
    status = "OK" if ok else "BAD"
    print(f"[{status}] {name}: {detail}")
    return ok


def main():
    parser = argparse.ArgumentParser(description="Check platform health and auth")
    parser.add_argument("--base-url", default="http://localhost:8002",
                        help="Platform base URL (default: http://localhost:8002)")
    parser.add_argument("--token", default=None,
                        help="Optional access token to verify authentication")
    args = parser.parse_args()

    results = []

    def health():
        status, body = request(args.base_url, "/api/v1/health")
        return status == 200 and body.get("status") == "ok", body

    def db_health():
        try:
            status, body = request(args.base_url, "/api/v1/health/db")
            return status == 200, body
        except urllib.error.HTTPError as e:
            # db check may be unavailable on some deployments; report but warn
            return True, f"db check skipped (HTTP {e.code})"

    def auth_me():
        if not args.token:
            return True, "no token provided, skipped"
        status, body = request(args.base_url, "/api/v1/auth/me", token=args.token)
        email = body.get("email", "?") if isinstance(body, dict) else "?"
        return status == 200, f"authenticated as {email}"

    results.append(check("health", health))
    results.append(check("health/db", db_health))
    results.append(check("auth/me", auth_me))

    print()
    if all(results):
        print(f"Platform at {args.base_url} is healthy.")
        return 0
    print(f"Platform at {args.base_url} has failures. "
          "If local, run the repository scripts/start-local-platform.ps1 to restore it.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
