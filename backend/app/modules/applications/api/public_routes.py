"""What somebody outside sees when they open a share link.

This is the only unauthenticated route in the platform, so it is written to be
read suspiciously. Three properties do the work:

* the token is the whole credential, and it is looked up rather than decoded,
  so a revoked link is dead the moment the row changes;
* nothing here writes, and nothing here accepts an id from the caller - the
  application decides what is rendered, so a link to one dashboard cannot be
  edited into a link to another;
* an invalid link is a 404 rather than a 403, so probing tells you nothing
  about what exists.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from app.api.deps import ApplicationServiceDep, DashboardServiceDep

from .rendering import render_application

router = APIRouter(tags=["public"])


@router.get(
    "/public/applications/{token}",
    summary="Open a shared application. No account required.",
)
def open_shared_application(
    token: str,
    applications: ApplicationServiceDep,
    dashboards: DashboardServiceDep,
) -> dict[str, Any]:
    """Render a shared application for a reader who has only the link."""
    application = applications.shared(token)

    #  Rendered here rather than returning ids for the client to fetch: a
    #  reader with only a link has no way to fetch anything else, and giving
    #  them ids they cannot use would invite a second, less careful endpoint.
    return {
        **render_application(application, dashboards),
        "shared_at": application.shared_at,
    }
