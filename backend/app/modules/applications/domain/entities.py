"""Applications: the last link of the chain.

An Application packages models, datasets and dashboards into something a person
uses rather than something a person builds. Publishing makes it reachable
inside the platform; sharing makes it reachable outside.

A share link is a capability: holding the URL is the permission. That makes it
the same kind of thing as a document link, with the same consequences - it is
long and random so it cannot be guessed, it grants reading and nothing else,
and it can be revoked without touching the application it points at.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from app.shared.ids import new_id, slugify, utcnow


class ApplicationKind(str, Enum):
    BUILTIN = "builtin"      # ships with the platform, has its own UI
    COMPOSED = "composed"    # assembled from models and dashboards


class ApplicationStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Visibility(str, Enum):
    """Who can open this application.

    Deliberately two values rather than a spectrum: "anyone signed in" and
    "anyone with the link" are the two questions people actually ask, and a
    third setting nobody understands is worse than a choice they do.
    """

    WORKSPACE = "workspace"   # members of the workspace, signed in
    LINK = "link"             # plus anybody holding the share link


def new_share_token() -> str:
    """A token long enough that guessing is not a strategy."""
    import secrets

    return secrets.token_urlsafe(32)



@dataclass
class Application:
    name: str
    kind: ApplicationKind = ApplicationKind.COMPOSED
    slug: str = ""
    description: str = ""
    status: ApplicationStatus = ApplicationStatus.DRAFT
    model_ids: list[str] = field(default_factory=list)
    dataset_ids: list[str] = field(default_factory=list)
    dashboard_ids: list[str] = field(default_factory=list)
    configuration: dict[str, Any] = field(default_factory=dict)
    #  Route the front end opens for built-in applications.
    entrypoint: str | None = None
    visibility: Visibility = Visibility.WORKSPACE
    #  Present only while a link is live. Revoking clears it, which is what
    #  makes the old URL stop working rather than merely stop being advertised.
    share_token: str | None = None
    shared_at: datetime | None = None
    id: str = field(default_factory=lambda: new_id("app"))
    created_at: datetime = field(default_factory=utcnow)
    #  Who made it, and where it lives. Recorded on the row when it is
    #  first written; carried here so the answer reaches a reader without
    #  a trip through the audit log.
    created_by: str | None = None
    workspace_id: str | None = None
    updated_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.slug:
            self.slug = slugify(self.name)

    @property
    def is_shared(self) -> bool:
        return self.visibility is Visibility.LINK and bool(self.share_token)

    def share(self) -> str:
        """Start sharing, or return the link already in use.

        Re-sharing keeps the existing token rather than issuing a new one: a
        link somebody has already sent to a colleague should not stop working
        because the owner pressed the button twice.
        """
        if not self.share_token:
            self.share_token = new_share_token()
        self.visibility = Visibility.LINK
        self.shared_at = utcnow()
        self.updated_at = self.shared_at
        return self.share_token

    def unshare(self) -> None:
        """Stop sharing, and make the old link dead rather than merely hidden."""
        self.share_token = None
        self.shared_at = None
        self.visibility = Visibility.WORKSPACE
        self.updated_at = utcnow()


