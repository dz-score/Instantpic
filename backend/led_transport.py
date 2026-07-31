"""Transport seam for the LED node — carries one line, returns one reply line.

This is the Pi-side mirror of the firmware's `transport.h`. The contract carries
over; the mechanism does not. The firmware selects its transport at compile time
and needs only a shared signature, while this side selects from config at
runtime and therefore needs a real interface.

The protocol is in Docs/LED_PROTOCOL.md. Three points from it shape this module:

  * `ERR` replies come back as **HTTP 200**. The request was well-formed, the
    command was not. Status codes say nothing about command success, so this
    layer returns the body verbatim and never interprets it.
  * **One command may be in flight at a time.** `transport_common.c` holds a
    single reply queue of depth 1, so command/reply correlation is positional
    rather than keyed. Serialization is enforced structurally by the single
    owner task in led_controller, not here.
  * Transport failures and `ERR` replies are different things. A failure raises;
    an `ERR` line is an ordinary return value. Collapsing the two would make a
    rejected command indistinguishable from an unreachable node.

Adding UART later means adding a class here with the same three methods. See
Docs/LED_UART_SWITCH.md for what that costs and what must already be true.
"""

from typing import Protocol

import httpx


class LedLinkError(Exception):
    """The line could not be delivered, or no reply came back.

    Distinct from an `ERR ...` reply, which means the node answered and said no.
    """


class LedLinkDown(LedLinkError):
    """The node could not be reached at all."""


class LedLinkTimeout(LedLinkError):
    """The node did not answer within the timeout.

    Ambiguous by nature: the command may still have been applied. The firmware's
    own `ERR TIMEOUT` carries the same ambiguity one layer down.
    """


class LedTransport(Protocol):
    """One line out, one reply line back."""

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, line: str, timeout_s: float) -> str:
        """Deliver `line`, return the reply line verbatim.

        Raises LedLinkDown / LedLinkTimeout. Never raises for an `ERR` reply.
        """
        ...

    async def resync(self) -> None:
        """Discard any partial or orphaned reply state before the next command."""
        ...


class LedHttpTransport:
    """HTTP transport — the one that ships. POSTs the raw line to /cmd.

    A single client with keep-alive, so commands do not each pay a TCP
    handshake. The connection limit is 1: the protocol allows only one command
    in flight, and a pool that could open a second connection would quietly
    permit the thing the design forbids.
    """

    def __init__(self, host: str, *, timeout_s: float):
        self._host = host.strip()
        self._timeout_s = timeout_s
        self._client: httpx.AsyncClient | None = None

    @property
    def description(self) -> str:
        return f"http://{self._host}"

    async def start(self) -> None:
        # No work in a constructor (Rule 19) — the client is created here so the
        # object can be built, injected and shut down on demand.
        self._client = httpx.AsyncClient(
            base_url=f"http://{self._host}",
            timeout=self._timeout_s,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def send(self, line: str, timeout_s: float) -> str:
        if self._client is None:
            raise LedLinkDown("transport not started")

        try:
            resp = await self._client.post("/cmd", content=line.encode("ascii"),
                                           timeout=timeout_s)
        except httpx.TimeoutException as e:
            raise LedLinkTimeout(f"no reply within {timeout_s:.3f}s") from e
        except httpx.HTTPError as e:
            raise LedLinkDown(str(e)) from e

        # Every documented reply, including every ERR, is a 200. Anything else
        # is the server misbehaving rather than the command being refused.
        if resp.status_code != 200:
            raise LedLinkDown(f"unexpected status {resp.status_code}")

        return resp.text.strip()

    async def resync(self) -> None:
        """No-op. Each HTTP request is self-framed, so there is nothing to drain.

        Present because UART needs it and the seam must not grow a method on the
        day it is swapped. Over a byte stream a single desync — a stale half-line,
        a reply arriving after its timeout — shifts the positional correlation
        and poisons every reply after it.
        """
        return None
