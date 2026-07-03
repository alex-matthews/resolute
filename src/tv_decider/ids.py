"""Sortable unique decision/feedback IDs (ULID-style, no external dependency)."""

from __future__ import annotations

import os
import time

_ENCODING = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32


def _encode(value: int, length: int) -> str:
    chars = []
    for _ in range(length):
        chars.append(_ENCODING[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


def new_id() -> str:
    """Return a 26-char lexicographically sortable id (timestamp ms + randomness)."""
    timestamp = int(time.time() * 1000)
    randomness = int.from_bytes(os.urandom(10), "big")
    return _encode(timestamp, 10) + _encode(randomness, 16)
