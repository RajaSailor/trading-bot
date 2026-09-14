#!/usr/bin/env python3
"""Container/service health check utility."""

from __future__ import annotations

import os

import requests


def check_endpoint(base_url: str, path: str) -> tuple[bool, str]:
    url = f"{base_url.rstrip('/')}{path}"
    try:
        response = requests.get(url, timeout=5)
        if 200 <= response.status_code < 300:
            return True, f"OK {url} ({response.status_code})"
        return False, f"FAIL {url} ({response.status_code})"
    except Exception as exc:  # pragma: no cover - defensive in runtime script
        return False, f"FAIL {url} ({exc})"


def main() -> int:
    base_url = os.getenv("HEALTH_BASE_URL", "http://localhost:5000")
    checks = ["/health", "/dhan/health"]

    failures = []
    for path in checks:
        ok, message = check_endpoint(base_url, path)
        print(message)
        if not ok:
            failures.append(message)

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
