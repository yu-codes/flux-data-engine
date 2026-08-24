"""What the platform is allowed to connect to.

A data source is a URL somebody types into a form, and the server fetches it.
That is server-side request forgery by construction: without a policy, the
platform is a credentialled proxy into whatever network it happens to sit in,
and the most valuable target is usually one hop away - a cloud metadata
endpoint at 169.254.169.254, an unauthenticated admin port on localhost, a
database on the same subnet.

So the rule is the opposite of the usual one: an address is refused unless it
is demonstrably on the public internet. Deployments that genuinely need to
reach an internal host say so explicitly, once, in configuration.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from app.shared.errors import ValidationError

ALLOWED_SCHEMES = ("http", "https")

#  Following a redirect is following a URL the platform did not validate, so
#  each hop is re-checked and the chain is short by design.
MAX_REDIRECTS = 3


@dataclass(frozen=True)
class NetworkPolicy:
    """Where outbound connections may go.

    `allow_private` is the escape hatch for a deployment whose data really does
    live on the internal network. It is off by default because the safe choice
    has to be the one you get without thinking about it.
    """

    allow_private: bool = False
    allowed_hosts: tuple[str, ...] = ()

    def permits_host(self, host: str) -> bool:
        return host.lower() in {h.lower() for h in self.allowed_hosts}


def check_url(url: str, policy: NetworkPolicy, *, schemes=ALLOWED_SCHEMES) -> str:
    """Refuse a URL that resolves anywhere the platform should not reach.

    Returns the hostname so callers can log or reuse it.

    Known limit, stated rather than hidden: the name is resolved here and
    connected to later, so a DNS entry that changes in between (rebinding) is
    not defeated by this check alone. Closing that needs connection-level
    pinning; what this does close is the whole class of "paste an internal URL
    and read the response", which is how it is actually exploited.
    """
    parsed = urlparse(url)
    if parsed.scheme.lower() not in schemes:
        raise ValidationError(
            f"url scheme must be one of {list(schemes)}, got '{parsed.scheme}'"
        )
    host = parsed.hostname
    if not host:
        raise ValidationError("url has no host")

    #  An explicit allow-list entry is a deliberate decision already made.
    if policy.permits_host(host):
        return host

    for address in _resolve(host):
        if _is_public(address):
            continue
        if policy.allow_private:
            continue
        raise ValidationError(
            f"'{host}' resolves to {address}, which is not a public address. "
            "Set FLUX_OUTBOUND_ALLOW_PRIVATE=true or add the host to "
            "FLUX_OUTBOUND_ALLOWED_HOSTS if this deployment is meant to reach it.",
            details={"host": host, "address": str(address)},
        )
    return host


def _resolve(host: str) -> list[ipaddress._BaseAddress]:
    """Every address the name answers with - one bad entry is enough to refuse."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise ValidationError(f"could not resolve host '{host}'") from exc

    addresses = []
    for info in infos:
        raw = info[4][0]
        try:
            addresses.append(ipaddress.ip_address(raw))
        except ValueError:
            continue
    if not addresses:
        raise ValidationError(f"could not resolve host '{host}' to an IP address")
    return addresses


def _is_public(address: ipaddress._BaseAddress) -> bool:
    """`is_global` alone is not enough on every Python/platform combination.

    The explicit checks below are the ones that matter in practice, and they
    are cheap: link-local covers the cloud metadata endpoint, private covers
    RFC1918 and unique-local, loopback covers the machine itself.
    """
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    #  An IPv4-mapped IPv6 address hides a v4 address inside a v6 one.
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        return _is_public(mapped)
    return True
