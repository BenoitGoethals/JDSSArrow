"""Integration endpoint — the coordinates a new client needs to join *this* JDSS network.

Returns the live network parameters (multicast endpoint, codec, security, coalition key,
authority public key, message types) so the Connect tab can render copy-paste onboarding
snippets that always match the running node.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from jdssarrow.capabilities import MESSAGE_TYPE_NAMES
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.web.deps import get_gateway

router = APIRouter(tags=["connect"])


@router.get("/api/connect")
def connect_info(gateway: JdssGateway = Depends(get_gateway)) -> dict:
    group, port = gateway.endpoint
    c = gateway.config
    return {
        "network_id": c.network.network_id,
        "multicast_group": group,
        "port": port,
        "codec": c.plugins.codec,
        "codec_content_type": gateway.codec.content_type,
        "security": c.plugins.security,
        "psk": c.network.psk,
        "repeat": c.network.repeat,
        "classification": c.classification.level,
        "releasable_to": c.classification.releasable_to,
        "message_types": MESSAGE_TYPE_NAMES,
        "policy_authority": c.connections.policy_authority,
        "authority_public_key": c.connections.authority_public_key,
        "frame": {
            "magic": "JDSS",
            "version": 1,
            "layout": "magic(4) | version(1) | codec-name | security-name | len(4) | payload",
            "payload": "security.protect( codec.encode(JdssMessage) )",
        },
    }
