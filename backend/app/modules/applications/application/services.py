"""Application and deployment services."""

from __future__ import annotations

from typing import Any

from app.shared.errors import ConflictError, NotFoundError, ValidationError
from app.shared.ids import slugify

from ..domain.entities import (
    Application,
    ApplicationKind,
    ApplicationStatus,
)
from ..domain.ports import ApplicationRepository


class ApplicationService:
    def __init__(self, repository: ApplicationRepository):
        self.repository = repository

    def create(
        self,
        *,
        name: str,
        description: str = "",
        kind: str = ApplicationKind.COMPOSED.value,
        model_ids: list[str] | None = None,
        dataset_ids: list[str] | None = None,
        dashboard_ids: list[str] | None = None,
        configuration: dict[str, Any] | None = None,
        entrypoint: str | None = None,
    ) -> Application:
        if self.repository.get_by_name(name):
            raise ConflictError(f"an application named '{name}' already exists")
        return self.repository.add(
            Application(
                name=name,
                description=description,
                kind=ApplicationKind(kind),
                slug=self._unique_slug(name),
                model_ids=model_ids or [],
                dataset_ids=dataset_ids or [],
                dashboard_ids=dashboard_ids or [],
                configuration=configuration or {},
                entrypoint=entrypoint,
            )
        )

    def get(self, application_id: str) -> Application:
        entity = self.repository.get(application_id) or self.repository.get_by_slug(
            application_id
        )
        if not entity:
            raise NotFoundError(f"application '{application_id}' not found")
        return entity

    def list(self) -> list[Application]:
        return self.repository.list()

    def update(self, application_id: str, changes: dict) -> Application:
        entity = self.get(application_id)
        for key in ("description", "entrypoint"):
            if changes.get(key) is not None:
                setattr(entity, key, changes[key])
        for key in ("model_ids", "dataset_ids", "dashboard_ids"):
            if changes.get(key) is not None:
                setattr(entity, key, list(changes[key]))
        if changes.get("configuration") is not None:
            entity.configuration = changes["configuration"]
        if changes.get("status"):
            entity.status = ApplicationStatus(changes["status"])
        return self.repository.update(entity)

    def publish(self, application_id: str) -> Application:
        """Make it reachable - by its own page, or at a stated entrypoint.

        The rule being kept is that publishing must not make nothing
        reachable. It used to be enforced by demanding an `entrypoint`, which
        meant every composed application had to name a route somebody had
        written by hand - and a built-in one had to have a page compiled into
        the frontend before it could exist at all.

        A composed application now has a page of its own, so what it needs is
        something to show on it. A built-in one still names its route, because
        its page is the point.
        """
        application = self.get(application_id)
        if application.kind is ApplicationKind.BUILTIN and application.entrypoint is None:
            raise ValidationError(
                "a built-in application is its own page, so it needs an "
                "entrypoint before it can be published"
            )
        if (
            application.kind is not ApplicationKind.BUILTIN
            and not application.dashboard_ids
            and application.entrypoint is None
        ):
            raise ValidationError(
                "this application has nothing to open: add a dashboard to it, "
                "or give it an entrypoint"
            )
        return self.update(application_id, {"status": ApplicationStatus.PUBLISHED.value})

    def unpublish(self, application_id: str) -> Application:
        """Take it back to draft.

        This is what a Deployment's "stop" was reaching for. Nothing is torn
        down because nothing was stood up: the application stops being offered,
        and everything it refers to is untouched.
        """
        application = self.get(application_id)
        #  A link that outlived unpublishing would mean an application could be
        #  withdrawn from everybody inside and still be open to the internet.
        application.unshare()
        self.repository.update(application)
        return self.update(application_id, {"status": ApplicationStatus.DRAFT.value})

    # -- sharing -----------------------------------------------------------
    def share(self, application_id: str) -> Application:
        """Give this application a link anybody can open.

        Only a published application can be shared: sharing a draft would put a
        half-built thing in front of somebody outside, and the fix for that is
        to publish it rather than to allow it.
        """
        application = self.get(application_id)
        if application.status is not ApplicationStatus.PUBLISHED:
            raise ValidationError(
                "publish this application before sharing it: a draft is not "
                "ready for somebody outside to open"
            )
        application.share()
        return self.repository.update(application)

    def unshare(self, application_id: str) -> Application:
        application = self.get(application_id)
        application.unshare()
        return self.repository.update(application)

    def shared(self, token: str) -> Application:
        """The application behind a share link, if the link is still live."""
        application = self.repository.get_by_share_token(token)
        if application is None or not application.is_shared:
            #  Not found rather than forbidden: a revoked link should look the
            #  same as one that never existed, so a probe learns nothing.
            raise NotFoundError("this link is not valid")
        if application.status is not ApplicationStatus.PUBLISHED:
            raise NotFoundError("this link is not valid")
        return application

    def delete(self, application_id: str) -> None:
        entity = self.get(application_id)
        if entity.kind is ApplicationKind.BUILTIN:
            raise ValidationError("built-in applications cannot be deleted")
        self.repository.delete(entity.id)

    # -- internals ---------------------------------------------------------
    def _unique_slug(self, name: str) -> str:
        base = slugify(name)
        slug, suffix = base, 2
        while self.repository.get_by_slug(slug):
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug


