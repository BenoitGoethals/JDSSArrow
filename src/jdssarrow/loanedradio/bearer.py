"""Loaned-radio bearer (Vol III).

The loaned radio is AEP-76's central idea: a coalition partner physically lends a radio to
another national system so the two can exchange C2 data directly at the tactical edge. In
software terms a :class:`RadioBearer` *owns* a link resource and *exposes a transport* to
the borrowing system. Keeping "the radio" separate from "the transport" lets the same
exchange engine run over a simulated radio, a UDP mesh, or (in future) a real waveform SDK.

:class:`SimulatedRadio` wraps any transport factory, modelling join/leave against a network.
"""

from __future__ import annotations

from collections.abc import Callable

from jdssarrow.interfaces import Transport


class SimulatedRadio:
    name = "simulated"

    def __init__(
        self,
        radio_id: str,
        transport_factory: Callable[[str], Transport],
    ) -> None:
        self.radio_id = radio_id
        self._make_transport = transport_factory
        self._transport: Transport | None = None
        self._network_id: str | None = None

    async def join(self, network_id: str) -> None:
        # "Joining" a network selects and constructs the bearer's transport (addressing is
        # baked in by the factory). The physical socket is opened by whoever owns the
        # transport lifecycle — the ExchangeEngine — so there is a single start/stop owner.
        self._network_id = network_id
        self._transport = self._make_transport(network_id)

    async def leave(self) -> None:
        self._transport = None
        self._network_id = None

    def transport(self) -> Transport:
        if self._transport is None:
            raise RuntimeError(f"radio {self.radio_id} has not joined a network")
        return self._transport
