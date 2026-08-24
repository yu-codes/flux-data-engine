"""Issuing, checking and revoking API keys."""

from __future__ import annotations

from datetime import timedelta

from app.shared.errors import NotFoundError, ValidationError
from app.shared.ids import utcnow

from ..domain.api_keys import ApiKey, generate_key, hash_key


class ApiKeyService:
    def __init__(self, repository):
        self.repository = repository

    def issue(
        self,
        *,
        name: str,
        workspace_id: str,
        can_write: bool = False,
        expires_in_days: int | None = None,
        created_by: str | None = None,
    ) -> tuple[ApiKey, str]:
        """Create a key and return it with its plaintext, once.

        The caller is expected to show the plaintext to whoever asked and then
        forget it. Nothing stores it, so there is nothing to show again.
        """
        if not name.strip():
            raise ValidationError("a key needs a name saying what it is for")
        secret, key_hash, hint = generate_key()
        key = self.repository.add(
            ApiKey(
                name=name.strip(),
                workspace_id=workspace_id,
                key_hash=key_hash,
                hint=hint,
                can_write=can_write,
                created_by=created_by,
                expires_at=(
                    utcnow() + timedelta(days=expires_in_days)
                    if expires_in_days
                    else None
                ),
            )
        )
        return key, secret

    def resolve(self, secret: str) -> ApiKey | None:
        """The key behind a presented secret, if it is still usable.

        Looked up by hash, so a stolen database yields nothing usable. An
        expired or revoked key resolves to nothing rather than to a key that
        happens to be inactive - a caller that has to remember to check is a
        caller that will forget.
        """
        if not secret:
            return None
        key = self.repository.get_by_hash(hash_key(secret))
        if key is None or not key.is_active:
            return None
        key.used()
        return self.repository.update(key)

    def list(self, workspace_id: str | None = None) -> list[ApiKey]:
        return self.repository.list(workspace_id=workspace_id)

    def revoke(self, key_id: str) -> ApiKey:
        key = self.repository.get(key_id)
        if key is None:
            raise NotFoundError(f"api key '{key_id}' not found")
        key.revoke()
        return self.repository.update(key)

    def delete(self, key_id: str) -> None:
        self.repository.delete(key_id)
