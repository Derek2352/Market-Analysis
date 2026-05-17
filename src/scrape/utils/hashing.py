from __future__ import annotations

import hashlib
import os


def hash_author(raw_author: str, *, salt: str | None = None) -> str:
    """Hash an author identifier with sha256 + a per-install salt.

    Salt comes from $AUTHOR_HASH_SALT unless `salt` is passed explicitly
    (tests pass it directly). Empty input returns the empty string so callers
    can distinguish anonymous posts without leaking a hash.
    """
    if not raw_author:
        return ""
    if salt is None:
        salt = os.environ.get("AUTHOR_HASH_SALT", "")
        if not salt:
            raise RuntimeError(
                "AUTHOR_HASH_SALT is not set. Put a long random string in .env."
            )
    h = hashlib.sha256()
    h.update(salt.encode("utf-8"))
    h.update(b":")
    h.update(raw_author.encode("utf-8"))
    return h.hexdigest()
