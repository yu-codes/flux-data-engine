"""What a database source is allowed to run.

A database source exists to read a table. The previous implementation passed
whatever string arrived straight to `text()`, which made "register a data
source" and "execute arbitrary SQL on any reachable database" the same
permission. This narrows it to what the feature actually needs: one statement,
and that statement reads.

This is not a SQL parser and does not pretend to be one. It is a gate that
refuses anything it cannot recognise as a single read - which is the correct
failure direction for a gate.
"""

from __future__ import annotations

import re

from app.shared.errors import ValidationError

#  Statement forms that only read. CTEs are included because a real analytical
#  query usually starts with WITH.
_READ_PREFIXES = ("select", "with")

#  Anything that writes, changes structure, grants rights, or reaches outside
#  the database. Matched as whole words anywhere in the statement, because
#  `SELECT ... INTO`, a CTE containing `DELETE`, and `COPY ... TO PROGRAM` all
#  hide the dangerous verb after a harmless-looking first word.
_FORBIDDEN = (
    "insert", "update", "delete", "merge", "truncate", "drop", "alter",
    "create", "replace", "grant", "revoke", "commit", "rollback", "savepoint",
    "vacuum", "analyze", "attach", "detach", "copy", "call", "do", "execute",
    "prepare", "listen", "notify", "load", "lock", "reindex", "reset", "set",
    "into", "pg_read_file", "pg_sleep", "dbms_lock", "xp_cmdshell",
)

_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_WORD = re.compile(r"[a-z_]+")


def assert_read_only(query: str) -> str:
    """Return the query if it is a single read, otherwise refuse it."""
    if not query or not query.strip():
        raise ValidationError("database source requires a non-empty query")

    stripped = _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", query))
    normalised = stripped.strip().rstrip(";").strip()
    if not normalised:
        raise ValidationError("the query is empty once comments are removed")

    #  A second statement is how a read turns into a write.
    if ";" in normalised:
        raise ValidationError(
            "a database source runs exactly one statement; ';' is not allowed"
        )

    lowered = normalised.lower()
    first = _WORD.match(lowered)
    if not first or first.group(0) not in _READ_PREFIXES:
        raise ValidationError(
            "a database source may only run SELECT or WITH",
            details={"starts_with": (first.group(0) if first else normalised[:20])},
        )

    words = set(_WORD.findall(lowered))
    offending = sorted(words & set(_FORBIDDEN))
    if offending:
        raise ValidationError(
            "this query contains keywords a data source may not use",
            details={"keywords": offending},
        )
    return normalised


_IDENTIFIER = r"[A-Za-z_][A-Za-z0-9_]*"
_TABLE_REFERENCE = re.compile(rf"{_IDENTIFIER}(\.{_IDENTIFIER})?")


def safe_table_name(name: str) -> str:
    """A table reference, optionally schema-qualified, and nothing else."""
    if not name or not _TABLE_REFERENCE.fullmatch(name):
        raise ValidationError(f"unsafe table name: {name}")
    return name
