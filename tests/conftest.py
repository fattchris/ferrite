"""Pytest configuration: load .env before tests run.

This ensures NEO4J_PASSWORD and other secrets from .env are available
to test modules that use os.environ.get() with defaults.
"""
import os
from pathlib import Path

_env_file = Path(__file__).parent.parent / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            # Don't override existing env vars
            if key not in os.environ:
                os.environ[key] = value
