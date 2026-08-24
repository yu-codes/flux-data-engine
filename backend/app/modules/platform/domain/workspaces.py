"""Workspaces: who a resource belongs to.

Every named thing in the platform used to be globally unique. Two people could
not both have a dataset called "Sales", a team could not keep a dev and a prod
copy of the same pipeline, and nothing recorded who created anything - "who
changed this model" was answerable only by reading the audit log sideways.

A workspace is the container that fixes all three. Names are unique within one,
not across the installation; a resource carries the workspace it belongs to and
the person who made it; and a role is held *in* a workspace rather than over
the whole platform.

The `ModelScope` and `DatasetOrigin.INTERMEDIATE` patches were symptoms of the
same gap seen from a different angle - a single namespace filling with things
nobody wanted to see. Those are gone; this is the other half.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from app.shared.ids import new_id, slugify, utcnow

#  Every installation has one. Resources that existed before workspaces did,
#  and resources created by callers that name none, belong to it.
DEFAULT_WORKSPACE_ID = "ws_default"
DEFAULT_WORKSPACE_NAME = "Default workspace"


class WorkspaceRole(str, Enum):
    """What somebody may do inside one workspace.

    The same three words as the platform-wide roles, deliberately: a role is
    the same idea, and the only thing workspaces change is where it applies.
    """

    ADMIN = "admin"      # manage the workspace and who is in it
    EDITOR = "editor"    # build and run
    VIEWER = "viewer"    # read


@dataclass
class Workspace:
    name: str
    slug: str = ""
    description: str = ""
    #  The installation's default cannot be deleted: something has to own the
    #  resources that name no workspace.
    is_default: bool = False
    id: str = field(default_factory=lambda: new_id("ws"))
    created_by: str | None = None
    created_at: datetime = field(default_factory=utcnow)
    updated_at: datetime = field(default_factory=utcnow)

    def __post_init__(self) -> None:
        if not self.slug:
            self.slug = slugify(self.name)


@dataclass
class WorkspaceMembership:
    """One person's role in one workspace."""

    workspace_id: str
    user_id: str
    role: WorkspaceRole = WorkspaceRole.VIEWER
    id: str = field(default_factory=lambda: new_id("wsm"))
    created_at: datetime = field(default_factory=utcnow)
