"""Turning an application into something a page can draw.

An `Application` holds three lists of ids. That is the right shape for the
domain - it names things without needing to know how any of them work, which is
what keeps this module at the bottom of the dependency stack - and the wrong
shape for a reader, who wants the charts.

So the assembling happens here, at the API layer, where both services are
already injected. Two endpoints use it: the shared link, whose reader has no
account, and the application's own page, whose reader has one. They render the
same thing on purpose - what somebody shares should be what they saw.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def render_application(
    application, dashboards, models=None, datasets=None
) -> dict[str, Any]:
    """The application as a page: its dashboards drawn, and its models runnable.

    `models` is optional because the shared view deliberately does without it:
    a link holder gets to read, not to spend the platform's compute. Inside,
    an application that bundles models should let somebody use them - that is
    the whole product, and until now the model ids were a list nothing
    rendered.
    """
    rendered = []
    for dashboard_id in application.dashboard_ids:
        try:
            rendered.append(dashboards.render(dashboard_id))
        except Exception as exc:  # noqa: BLE001 - one bad tile is not a bad page
            #  A chart whose dataset was deleted must not take the whole page
            #  down for a reader who cannot see why it broke.
            logger.warning(
                "application '%s': dashboard %s could not be rendered: %s",
                application.name,
                dashboard_id,
                exc,
            )

    return {
        "name": application.name,
        "description": application.description,
        "slug": application.slug,
        "dashboards": rendered,
        "tools": _tools(application, models) if models is not None else [],
        #  What its tools may be run against. The application chose these, so
        #  offering the whole catalogue instead would make that choice
        #  meaningless - and the shared view gets none of them.
        "datasets": _datasets(application, datasets) if models is not None else [],
        #  Counts rather than contents: enough to say what this is built from,
        #  without handing out the catalogue.
        "built_from": {
            "models": len(application.model_ids),
            "datasets": len(application.dataset_ids),
            "dashboards": len(application.dashboard_ids),
        },
    }


def _tools(application, models) -> list[dict[str, Any]]:
    """The application's models, described well enough to build a form from.

    The contracts travel with the model, so the page needs nothing from the
    registry and a provider added tomorrow gets a form for free - the same
    property that makes `ContractForm` worth having in the first place.
    """
    tools = []
    for model_id in application.model_ids:
        try:
            model = models.get(model_id)
            kinds = [kind.value for kind in models.supported_kinds(model_id)]
        except Exception as exc:  # noqa: BLE001 - a deleted model is not a broken page
            logger.warning(
                "application '%s': model %s is not available: %s",
                application.name, model_id, exc,
            )
            continue
        tools.append(
            {
                "model_id": model.id,
                "name": model.name,
                "description": model.description,
                "provider": model.provider,
                "kinds": kinds,
                "parameter_contract": model.parameter_contract.to_dict(),
                "input_contract": model.input_contract.to_dict(),
            }
        )
    return tools


def _datasets(application, datasets) -> list[dict[str, Any]]:
    """The datasets this application bundles, named."""
    if datasets is None:
        return []
    named = []
    for dataset_id in application.dataset_ids:
        try:
            dataset = datasets.get(dataset_id)
        except Exception as exc:  # noqa: BLE001 - a deleted dataset is not a broken page
            logger.warning(
                "application '%s': dataset %s is not available: %s",
                application.name, dataset_id, exc,
            )
            continue
        named.append({"id": dataset.id, "name": dataset.name})
    return named
