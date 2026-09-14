"""DhanHQ API configuration for Phase 2 integration."""

from __future__ import annotations

import os


class DhanHQConfig:
    """Centralized DhanHQ endpoint and timeout settings."""

    DEFAULT_API_BASE_URL = "https://api.dhan.co/v2"
    API_BASE_URL = os.getenv("DHANHQ_API_BASE_URL", DEFAULT_API_BASE_URL)

    ENDPOINTS = {
        "orders": "/orders",
        "positions": "/positions",
        "holdings": "/holdings",
        "funds": "/fundlimit",
        "historical": "/charts/historical",
        "intraday": "/charts/intraday",
        "quotes": "/marketfeed/quote",
    }

    TIMEOUT_SECONDS = int(os.getenv("DHANHQ_TIMEOUT_SECONDS", "10"))
    CONNECT_TIMEOUT_SECONDS = int(os.getenv("DHANHQ_CONNECT_TIMEOUT_SECONDS", "5"))
    READ_TIMEOUT_SECONDS = int(os.getenv("DHANHQ_READ_TIMEOUT_SECONDS", "10"))
    MAX_RETRIES = int(os.getenv("DHANHQ_MAX_RETRIES", "3"))

    @classmethod
    def endpoint_url(cls, key: str) -> str:
        """Return a fully-qualified URL for a configured endpoint key."""
        if key not in cls.ENDPOINTS:
            valid_keys = ", ".join(sorted(cls.ENDPOINTS))
            raise ValueError(f"Unknown DhanHQ endpoint key '{key}'. Valid keys: {valid_keys}")
        path = cls.ENDPOINTS[key]
        base_url = os.getenv("DHANHQ_API_BASE_URL", cls.DEFAULT_API_BASE_URL)
        return f"{base_url}{path}"
