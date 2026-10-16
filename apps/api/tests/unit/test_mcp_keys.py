"""Unit coverage for MCP API key generation and hashing."""

from __future__ import annotations

import re

from sourcemind.services.mcp_keys import generate_key, hash_key


def test_generate_key_has_expected_format_and_derived_values() -> None:
    key, prefix, digest = generate_key()

    assert key.startswith("smk_")
    assert len(key) == 47
    assert re.fullmatch(r"smk_[A-Za-z0-9_-]{43}", key)
    assert prefix == key[:8]
    assert digest == hash_key(key)
    assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_generate_key_is_unique() -> None:
    keys = {generate_key()[0] for _ in range(100)}
    assert len(keys) == 100


def test_hash_key_is_stable_and_sensitive_to_full_key() -> None:
    key = "smk_" + "a" * 43
    assert hash_key(key) == hash_key(key)
    assert hash_key(key) != hash_key(key[:-1] + "b")
