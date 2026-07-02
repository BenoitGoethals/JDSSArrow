"""Coalition-wide connection policy distribution over the gossip control channel.

One node is the **policy authority**. It owns the coalition connection policy and broadcasts
it as an out-of-band, HMAC-protected control message; every node applies it (layered under its
own local policy via :class:`CompositePolicy`), so a coalition-wide block/allow converges
across the whole network.

Trust model: control frames are authenticated by the shared coalition key, and nodes only
accept policy updates whose sender equals the configured ``authority_id`` — so a random member
cannot impersonate the authority *by node id*. (A member that already holds the key could still
spoof the id; a production system would sign updates with the authority's own key. Noted.)

Updates are versioned and monotonic: the authority bumps the version on every change and
re-broadcasts periodically, so late joiners converge and duplicates are ignored.
"""

from __future__ import annotations

import asyncio
import contextlib

from jdssarrow.connections.policy import MatrixConnectionPolicy
from jdssarrow.connections.signing import AuthoritySigner, AuthorityVerifier, canonical_payload
from jdssarrow.iem.exchange import ExchangeEngine

_KIND = "policyupdate"


class PolicyDistributor:
    def __init__(
        self,
        node_id: str,
        engine: ExchangeEngine,
        coalition_policy: MatrixConnectionPolicy,
        authority_id: str | None,
        interval_s: float = 2.0,
        signer: AuthoritySigner | None = None,
        verifier: AuthorityVerifier | None = None,
    ) -> None:
        self._node_id = node_id
        self._engine = engine
        self._policy = coalition_policy  # shared with the gateway's composite policy
        self._authority_id = authority_id
        self._is_authority = authority_id is not None and node_id == authority_id
        self._interval = max(0.05, interval_s)
        self._signer = signer
        self._verifier = verifier
        # authority seeds at version 1 (its config is the initial policy); others at 0.
        self._version = 1 if self._is_authority else 0
        self._task: asyncio.Task | None = None

    @property
    def signed(self) -> bool:
        """True when policy updates are cryptographically verified on this node."""
        return self._verifier is not None

    @property
    def is_authority(self) -> bool:
        return self._is_authority

    @property
    def version(self) -> int:
        return self._version

    @property
    def authority_id(self) -> str | None:
        return self._authority_id

    def attach(self) -> None:
        self._engine.on_control(_KIND, self._on_update)

    async def start(self) -> None:
        if self._is_authority:
            self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    # ------------------------------------------------------------- authority side
    async def _loop(self) -> None:
        while True:
            with contextlib.suppress(Exception):
                await self._broadcast()
            await asyncio.sleep(self._interval)

    async def _broadcast(self) -> None:
        snap = self._policy.snapshot()
        data = {
            "version": self._version,
            "default_action": snap["default_action"],
            "overrides": snap["overrides"],
        }
        if self._signer is not None:
            payload = canonical_payload(self._version, snap["default_action"], snap["overrides"])
            data["sig"] = self._signer.sign(payload)
        await self._engine.publish_control(_KIND, data)

    def _require_authority(self) -> None:
        if not self._is_authority:
            raise PermissionError("this node is not the coalition policy authority")

    async def block(self, peer_id: str) -> None:
        self._require_authority()
        self._policy.block(peer_id)
        await self._commit()

    async def allow(self, peer_id: str) -> None:
        self._require_authority()
        self._policy.allow(peer_id)
        await self._commit()

    async def reset(self, peer_id: str) -> None:
        self._require_authority()
        self._policy.reset(peer_id)
        await self._commit()

    async def set_default(self, action: str) -> None:
        self._require_authority()
        self._policy.set_default(action)
        await self._commit()

    async def _commit(self) -> None:
        self._version += 1
        await self._broadcast()

    # ------------------------------------------------------------- receiver side
    async def _on_update(self, data: dict) -> None:
        # accept only from the trusted authority, only strictly newer versions
        if data.get("node") != self._authority_id or self._is_authority:
            return
        version = int(data.get("version", 0))
        default_action = data.get("default_action", "allow")
        overrides = data.get("overrides", {})
        # per-authority signature verification: unforgeable even by a coalition-key holder
        if self._verifier is not None:
            payload = canonical_payload(version, default_action, overrides)
            if not self._verifier.verify(payload, data.get("sig", "")):
                return  # forged / unsigned / tampered → reject
        if version <= self._version:
            return
        self._version = version
        self._policy.replace(default_action, overrides)
