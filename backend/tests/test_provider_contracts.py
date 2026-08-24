"""A provider's contract must describe what the provider actually accepts.

The contract is not documentation. It is what the UI builds a form from, what
an experiment checks a trial against, and what tells a caller why their request
was refused. When it drifts from the code, every one of those quietly becomes
wrong - and nothing fails, because the provider validates its own input by
hand and never consults the contract at all.

That is exactly what happened: `optimizer` described its variables as a list
while reading them as a mapping, and the whole test suite passed. Every
provider ships worked examples; checking them against the provider's own
declared shape is the cheapest way to keep the two honest.
"""

from __future__ import annotations

import pytest

from app.plugins.bootstrap import register_builtin_plugins
from app.shared.contracts import Contract, ContractShape, FieldSpec, FieldType, VisibleWhen

REGISTRY = register_builtin_plugins()
DESCRIPTORS = {key: REGISTRY.get(key).describe() for key in REGISTRY.keys()}


def _examples(descriptor):
    return getattr(descriptor, "examples", []) or []


@pytest.mark.parametrize("key", sorted(DESCRIPTORS))
def test_every_provider_ships_a_worked_example(key):
    """An example is how somebody starts, and how this file has anything to check."""
    assert _examples(DESCRIPTORS[key]), f"provider '{key}' has no examples"


@pytest.mark.parametrize("key", sorted(DESCRIPTORS))
def test_a_providers_examples_satisfy_its_own_configuration_contract(key):
    descriptor = DESCRIPTORS[key]
    contract = descriptor.configuration_contract
    for example in _examples(descriptor):
        configuration = example.get("configuration")
        if configuration is None:
            continue
        result = contract.validate_record(configuration)
        assert result.valid, (
            f"provider '{key}' example '{example.get('name')}' does not satisfy "
            f"the configuration contract it publishes: {result.errors}"
        )


@pytest.mark.parametrize("key", sorted(DESCRIPTORS))
def test_a_providers_examples_satisfy_its_own_parameter_contract(key):
    descriptor = DESCRIPTORS[key]
    for example in _examples(descriptor):
        parameters = example.get("parameters")
        if parameters is None:
            continue
        result = descriptor.parameter_contract.validate_record(parameters)
        assert result.valid, (
            f"provider '{key}' example '{example.get('name')}' does not satisfy "
            f"the parameter contract it publishes: {result.errors}"
        )


@pytest.mark.parametrize("key", sorted(DESCRIPTORS))
def test_a_providers_example_is_accepted_by_the_provider_itself(key, client, api):
    """The other half: the contract and the provider must agree both ways.

    A contract that accepts everything would pass the tests above and tell the
    UI nothing. Creating the model runs the provider's own validation, so a
    published example that the provider rejects fails here.
    """
    descriptor = DESCRIPTORS[key]
    example = next(
        (e for e in _examples(descriptor) if e.get("configuration") is not None), None
    )
    if example is None:
        pytest.skip(f"provider '{key}' has no configuration example")

    response = client.post(
        f"{api}/models",
        json={
            "name": f"Contract check · {key} · {example['name']}",
            "provider": key,
            "configuration": example["configuration"],
        },
    )
    assert response.status_code == 201, (
        f"provider '{key}' rejects its own published example: {response.text}"
    )


# --------------------------------------------------------------------------
# the primitives themselves
# --------------------------------------------------------------------------
def test_a_nested_object_is_checked_field_by_field():
    contract = Contract(
        shape=ContractShape.OBJECT,
        fields=[
            FieldSpec(
                "window",
                FieldType.JSON,
                fields=(
                    FieldSpec("start", FieldType.INTEGER),
                    FieldSpec("end", FieldType.INTEGER),
                ),
            )
        ],
    )
    assert contract.validate_record({"window": {"start": 1, "end": 5}}).valid

    bad = contract.validate_record({"window": {"start": 1}})
    assert not bad.valid
    #  The path names the field inside the object, not just the object.
    assert "window.end" in " ".join(bad.errors)


def test_a_list_checks_every_element_against_the_same_shape():
    contract = Contract(
        shape=ContractShape.OBJECT,
        fields=[
            FieldSpec(
                "rules",
                FieldType.ARRAY,
                item=FieldSpec(
                    "rule",
                    FieldType.JSON,
                    fields=(
                        FieldSpec("when", FieldType.STRING),
                        FieldSpec("then", FieldType.JSON),
                    ),
                ),
            )
        ],
    )
    ok = contract.validate_record(
        {"rules": [{"when": "a > 1", "then": {"x": 1}}, {"when": "a > 2", "then": {}}]}
    )
    assert ok.valid, ok.errors

    bad = contract.validate_record({"rules": [{"then": {}}]})
    assert not bad.valid
    assert "rules[0].when" in " ".join(bad.errors)

    assert not contract.validate_record({"rules": {"not": "a list"}}).valid


def test_a_mapping_checks_every_value_against_the_same_shape():
    """Keys the user chooses, values that all look alike."""
    contract = Contract(
        shape=ContractShape.OBJECT,
        fields=[
            FieldSpec(
                "variables",
                FieldType.JSON,
                values=FieldSpec(
                    "variable",
                    FieldType.JSON,
                    fields=(
                        FieldSpec("min", FieldType.FLOAT),
                        FieldSpec("max", FieldType.FLOAT),
                    ),
                ),
            )
        ],
    )
    assert contract.validate_record(
        {"variables": {"price": {"min": 1, "max": 9}}}
    ).valid

    bad = contract.validate_record({"variables": {"price": {"min": 1}}})
    assert not bad.valid
    assert "variables.price.max" in " ".join(bad.errors)


def test_a_field_that_does_not_apply_is_not_required():
    """`degree` means nothing unless the family is polynomial."""
    contract = Contract(
        shape=ContractShape.OBJECT,
        fields=[
            FieldSpec("family", FieldType.STRING, enum=("linear", "polynomial")),
            FieldSpec(
                "degree",
                FieldType.INTEGER,
                visible_when=VisibleWhen("family", equals="polynomial"),
            ),
        ],
    )
    assert contract.validate_record({"family": "linear"}).valid
    assert not contract.validate_record({"family": "polynomial"}).valid
    assert contract.validate_record({"family": "polynomial", "degree": 2}).valid


def test_a_field_can_apply_to_several_values():
    contract = Contract(
        shape=ContractShape.OBJECT,
        fields=[
            FieldSpec("kind", FieldType.STRING),
            FieldSpec(
                "scale",
                FieldType.FLOAT,
                visible_when=VisibleWhen("kind", in_=("normal", "lognormal")),
            ),
        ],
    )
    assert contract.validate_record({"kind": "uniform"}).valid
    assert not contract.validate_record({"kind": "normal"}).valid
    assert contract.validate_record({"kind": "lognormal", "scale": 1.5}).valid


def test_coercion_descends_into_nested_shapes():
    contract = Contract(
        shape=ContractShape.OBJECT,
        fields=[
            FieldSpec(
                "variables",
                FieldType.JSON,
                values=FieldSpec(
                    "variable",
                    FieldType.JSON,
                    fields=(
                        FieldSpec("min", FieldType.FLOAT),
                        FieldSpec("step", FieldType.FLOAT, required=False, default=1),
                    ),
                ),
            )
        ],
    )
    coerced = contract.coerce_record({"variables": {"price": {"min": "10"}}})
    #  The string became a number and the default was applied, two levels down.
    assert coerced["variables"]["price"] == {"min": 10.0, "step": 1.0}


def test_a_contract_survives_a_round_trip_through_plain_data():
    """Contracts are stored as JSON in version snapshots and sent to the UI."""
    original = FieldSpec(
        "variables",
        FieldType.JSON,
        values=FieldSpec(
            "variable",
            FieldType.JSON,
            fields=(
                FieldSpec("kind", FieldType.STRING, enum=("range", "choices")),
                FieldSpec(
                    "min",
                    FieldType.FLOAT,
                    visible_when=VisibleWhen("kind", equals="range"),
                ),
            ),
        ),
    )
    restored = FieldSpec.from_dict(original.to_dict())
    assert restored == original
