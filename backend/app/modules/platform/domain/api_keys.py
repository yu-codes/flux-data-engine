"""API keys: how a system, rather than a person, calls the platform.

The only way to invoke a model was to exchange a human's password for a JWT and
then send it from your service. That is a person's credential doing a machine's
job: it expires on a human timescale, it carries a human's full permissions,
and revoking it means changing somebody's password.

A key belongs to a workspace, not to a person. Only its hash is stored, so a
leaked database does not leak working credentials, and the plaintext is shown
exactly once - at creation - because a system that can show you a key again is
a system that could show it to somebody else.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field
from datetime import datetime

from app.shared.ids import new_id, utcnow

#  Long enough that guessing is hopeless, short enough to paste.
KEY_BYTES = 32
PREFIX = "flux_"
#  The visible part, kept in clear so a key can be identified in a list without
#  being usable from it.
HINT_LENGTH = 8


def generate_key() -> tuple[str, str, str]:
    """A new key: the plaintext, its hash, and the hint to display.

    The plaintext is returned rather than stored. Whoever asked for it has this
    one chance to copy it.
    """
    secret = PREFIX + secrets.token_urlsafe(KEY_BYTES)
    return secret, hash_key(secret), secret[: len(PREFIX) + HINT_LENGTH]


def hash_key(secret: str) -> str:
    """SHA-256, not a password hash, and deliberately.

    A password is short, low-entropy and chosen by a human, so it needs a slow
    hash to survive a dictionary attack. A key is 256 bits of randomness: there
    is no dictionary, and a slow hash on every API call would cost real latency
    for no security.
    """
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


@dataclass
class ApiKey:
    """A credential a system holds, scoped to one workspace."""

    name: str
    workspace_id: str
    key_hash: str
    hint: str = ""
    #  What it may do. Deliberately narrow by default: a key exists to call
    #  models, and most of them never need to change anything.
    can_write: bool = False
    created_by: str | None = None
    last_used_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    id: str = field(default_factory=lambda: new_id("key"))
    created_at: datetime = field(default_factory=utcnow)

    @property
    def is_active(self) -> bool:
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=utcnow().tzinfo)
        return expires > utcnow()

    def revoke(self) -> None:
        self.revoked_at = utcnow()

    def used(self) -> None:
        self.last_used_at = utcnow()
