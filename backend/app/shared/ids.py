"""Identifier and slug helpers shared by every module."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime


def new_id(prefix: str) -> str:
    """Readable, sortable-enough identifier, e.g. ``ds_3f2a...``."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


def as_utc(value: datetime | None) -> datetime | None:
    """A stored timestamp, made comparable with `utcnow()`.

    Columns are declared `DateTime(timezone=True)`, but SQLite does not store
    an offset and hands back a naive datetime. Everything written goes in as
    UTC, so a naive value read from the database is UTC that lost its label -
    and subtracting it from an aware one raises `TypeError` rather than
    returning a wrong number. That is better than silent nonsense and worse
    than working, so the label is put back here, once, where rows become
    entities.
    """
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(value: str) -> str:
    """Lowercase kebab slug; non-ascii names fall back to a hashed suffix."""
    slug = _SLUG_STRIP.sub("-", value.strip().lower()).strip("-")
    if not slug:
        slug = f"item-{uuid.uuid4().hex[:8]}"
    return slug
