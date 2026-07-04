"""Configuration models.

A single ``GatewayConfig`` fully describes a node: its identity, which plugin implements
each extension point, and the network parameters. Because every pluggable choice is just a
string naming a registry entry (``codec: "xml"``, ``transport: "udp"`` …), reconfiguring the
system — even swapping in a third-party plugin — never requires a code change.

Layering (lowest→highest precedence): field defaults → config file → ``JDSS_`` env vars →
runtime overrides pushed from the web UI.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class NodeIdentity(BaseModel):
    node_id: str = "node-a"
    callsign: str = "ALPHA-1"
    unit: str = "1PL/A/1-501"
    nation: str = "XXX"
    role: str = "rifleman"


class PluginSelection(BaseModel):
    """Names resolved against the plugin registry groups."""

    codec: str = "xml"
    transport: str = "udp"
    security: str = "psk"
    bearer: str = "simulated"
    allocator: str = "default"


class NetworkConfig(BaseModel):
    network_id: str = "default"
    #: explicit multicast group; if None the allocator derives it from network_id.
    multicast_group: str | None = None
    multicast_port: int | None = None
    repeat: int = Field(default=2, ge=1, le=8)
    psk: str = "jdss-coalition-key"


class ClassificationConfig(BaseModel):
    """Default security marking stamped on messages this node originates."""

    #: 0=UNCLASSIFIED, 1=RESTRICTED, 2=CONFIDENTIAL, 3=SECRET.
    level: int = Field(default=0, ge=0, le=3)
    #: releasability caveat, e.g. "REL BEL NLD" or "ALL".
    releasable_to: str = "ALL"


class ConnectionsConfig(BaseModel):
    """Connection-management policy — this node's row of the coalition admit/deny matrix."""

    policy: str = "matrix"  # "matrix" (manageable) or "allow_all"
    default_action: str = "allow"  # "allow" | "deny"
    blocked: list[str] = Field(default_factory=list)
    allowed: list[str] = Field(default_factory=list)

    # --- coalition-wide policy distributed via gossip ---
    #: node_id trusted as the coalition policy authority (None = no distribution).
    policy_authority: str | None = None
    #: initial coalition policy (only meaningful on the authority node; it seeds & broadcasts).
    coalition_default_action: str = "allow"
    coalition_blocked: list[str] = Field(default_factory=list)
    coalition_allowed: list[str] = Field(default_factory=list)
    #: Ed25519 authority public key (hex) every node uses to verify policy updates.
    authority_public_key: str | None = None
    #: Ed25519 authority private key (hex) — only present on the authority; signs updates.
    authority_private_key: str | None = None


class CapabilitiesConfig(BaseModel):
    """Per-message-type receive/emit permissions (empty = everything allowed)."""

    receive: dict[str, bool] = Field(default_factory=dict)
    emit: dict[str, bool] = Field(default_factory=dict)


class ServerConnection(BaseModel):
    """A CoT/TAK server this node bridges to over TCP (optionally mutual TLS).

    Managed (CRUD) at runtime from the Configuration tab. Each enabled entry drives a live,
    auto-reconnecting connector; JDSS traffic from other nodes is relayed out as CoT and inbound
    CoT is re-published onto the JDSS network. Works with OpenTAKServer, FreeTAKServer and TAK
    Server (see README "OpenTAKServer": plaintext 8088, SSL 8089 with a client cert)."""

    id: str
    name: str = ""
    host: str = "127.0.0.1"
    port: int = Field(default=8089, ge=1, le=65535)
    tls: bool = False
    verify: bool = True  # verify the server certificate (TLS only)
    # cert/key/CA for mutual TLS: PEM content (e.g. imported from a .p12) or a filesystem path.
    # The client cert's CN must be a registered TAK user.
    client_cert: str | None = None
    client_key: str | None = None
    cacert: str | None = None
    enabled: bool = True


class EudServerConfig(BaseModel):
    """Built-in CoT/TAK server so ATAK / WinTAK / iTAK EUDs connect *directly* to this node.

    When enabled the node listens on a TCP port; a connecting EUD's CoT is translated to JDSS and
    published on the coalition net, and coalition traffic is streamed back to the EUD as CoT. In
    ATAK: add a TAK Server pointing at this host:port (TCP, no SSL). Managed from the Configuration
    tab."""

    enabled: bool = False
    host: str = "0.0.0.0"  # bind address; 0.0.0.0 = all interfaces
    port: int = Field(default=8087, ge=1, le=65535)
    #: address ATAK should point at (the host's LAN IP or a public/NAT address). Auto-detection
    #: returns the container's internal IP under Docker, so set this for reachable clients.
    advertised_host: str | None = None


class GossipConfig(BaseModel):
    """Peer-digest gossip: each node periodically broadcasts the row of the connection
    matrix it can see (who it has heard from), so every node can assemble the full matrix."""

    enabled: bool = True
    interval_s: float = 2.0


class WebConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    telemetry_capacity: int = 1000
    #: a peer is "connected" if seen within this many seconds.
    peer_timeout_s: int = 15


class GatewayConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="JDSS_", env_nested_delimiter="__")

    identity: NodeIdentity = Field(default_factory=NodeIdentity)
    plugins: PluginSelection = Field(default_factory=PluginSelection)
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    classification: ClassificationConfig = Field(default_factory=ClassificationConfig)
    connections: ConnectionsConfig = Field(default_factory=ConnectionsConfig)
    capabilities: CapabilitiesConfig = Field(default_factory=CapabilitiesConfig)
    gossip: GossipConfig = Field(default_factory=GossipConfig)
    web: WebConfig = Field(default_factory=WebConfig)
    #: CoT/TAK servers this node bridges to (managed from the Configuration tab).
    servers: list[ServerConnection] = Field(default_factory=list)
    #: built-in TAK server so ATAK EUDs can connect directly to this node.
    eud_server: EudServerConfig = Field(default_factory=EudServerConfig)
