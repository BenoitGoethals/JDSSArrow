"""``jdssarrow`` command-line entry point.

Launches a soldier node from a config file: joins the network, periodically emits Presence,
prints every message it receives, and (optionally) fires a one-off CASEVAC so a two-terminal
demo shows real message exchange over UDP multicast.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import signal
from collections.abc import Iterable

from jdssarrow.config.loader import load_config
from jdssarrow.datamodel.messages import JdssMessage
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode


class _PrintHandler:
    subscribes_to: Iterable[str] = ("*",)

    def __init__(self, node_id: str) -> None:
        self._node_id = node_id

    async def handle(self, message: JdssMessage) -> None:
        h = message.header
        print(f"[{self._node_id}] << {message.type} from {h.originator_id} (seq {h.sequence})")


async def _run(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    gateway = JdssGateway(config)
    node = SoldierNode(gateway)
    node.add_handler(_PrintHandler(config.identity.node_id))

    await node.start()
    group, port = gateway.endpoint
    print(
        f"[{config.identity.node_id}] {config.identity.callsign} joined "
        f"'{config.network.network_id}' on {group}:{port} "
        f"(codec={config.plugins.codec}, transport={config.plugins.transport}, "
        f"security={config.plugins.security})"
    )

    await node.identify()
    if args.casevac:
        await asyncio.sleep(1.0)
        await node.request_casevac(args.lat, args.lon, urgent=2)
        print(f"[{config.identity.node_id}] >> CASEVAC requested")

    stop = asyncio.Event()

    async def beacon() -> None:
        while not stop.is_set():
            await node.presence(args.lat, args.lon, battery_pct=90)
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=args.interval)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    beacon_task = asyncio.create_task(beacon())
    try:
        await stop.wait()
    finally:
        beacon_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await beacon_task
        await node.stop()
        print(f"[{config.identity.node_id}] stopped")


def main() -> None:
    parser = argparse.ArgumentParser(prog="jdssarrow", description="Run a JDSS soldier node.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run a soldier node")
    run.add_argument("--config", "-c", default=None, help="YAML/TOML config path")
    run.add_argument("--interval", type=float, default=5.0, help="presence beacon seconds")
    run.add_argument("--lat", type=float, default=50.8503, help="beacon latitude")
    run.add_argument("--lon", type=float, default=4.3517, help="beacon longitude")
    run.add_argument("--casevac", action="store_true", help="send one CASEVAC after join")

    sim = sub.add_parser("simulate", help="run a multi-client JDSS interoperability scenario")
    sim.add_argument("--ticks", type=int, default=20, help="number of behaviour ticks")
    sim.add_argument("--interval", type=float, default=0.05, help="seconds per tick")
    sim.add_argument(
        "--transport", default="loopback", choices=["loopback", "udp"], help="bearer"
    )
    sim.add_argument("--codec", default="xml", choices=["xml", "json", "arrow"])
    sim.add_argument("--network", default="exercise-jdss", help="coalition network id")
    sim.add_argument(
        "--rogue",
        choices=["wrong_key", "garbage", "insider"],
        help="inject a non-compliant client and verify the network rejects it",
    )
    sim.add_argument("--matrix", action="store_true", help="print the N×N connection matrix")

    br = sub.add_parser("bridge", help="bridge a non-JDSS client (e.g. ATAK/CoT) to JDSS")
    br.add_argument("kind", choices=["atak"], help="bridge type")
    br.add_argument("--config", "-c", default=None, help="JDSS node config (YAML/TOML)")
    br.add_argument("--cot-group", default="239.2.3.1", help="ATAK CoT multicast group")
    br.add_argument("--cot-port", type=int, default=6969, help="ATAK CoT multicast port")
    br.add_argument("--cot-stale", type=int, default=60, help="CoT track stale window (seconds)")
    br.add_argument("--tak-server", default=None, help="TAK Server host:port (TCP), not multicast")
    br.add_argument("--tak-tls", action="store_true", help="use TLS to the TAK Server")
    br.add_argument("--tak-cert", default=None, help="client certificate (PEM) for mutual TLS")
    br.add_argument("--tak-key", default=None, help="client private key (PEM)")
    br.add_argument("--tak-cacert", default=None, help="CA certificate (PEM) to verify the server")
    br.add_argument("--tak-insecure", action="store_true", help="skip TLS server verification")

    sub.add_parser("keygen", help="generate an Ed25519 coalition-authority signing keypair")

    args = parser.parse_args()
    if args.command == "run":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_run(args))
    elif args.command == "simulate":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_simulate(args))
    elif args.command == "bridge":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(_bridge(args))
    elif args.command == "keygen":
        from jdssarrow.connections.signing import generate_keypair

        priv, pub = generate_keypair()
        print("# add to the AUTHORITY node config under connections:")
        print(f"  authority_private_key: {priv}")
        print("# add to EVERY node config (incl. authority) under connections:")
        print(f"  authority_public_key: {pub}")


async def _bridge(args: argparse.Namespace) -> None:
    from jdssarrow.bridges.atak import AtakBridge

    config = load_config(args.config)
    cot_transport = None
    cot_desc = f"CoT mesh {args.cot_group}:{args.cot_port}"
    if args.tak_server:
        from jdssarrow.bridges.takserver import TakServerConnector

        host, _, port_s = args.tak_server.partition(":")
        tak_port = int(port_s or (8089 if args.tak_tls else 8087))
        cot_transport = TakServerConnector(
            host,
            tak_port,
            tls=args.tak_tls,
            client_cert=args.tak_cert,
            client_key=args.tak_key,
            cacert=args.tak_cacert,
            verify=not args.tak_insecure,
        )
        cot_desc = f"TAK Server {host}:{tak_port} ({'TLS' if args.tak_tls else 'TCP'})"

    bridge = AtakBridge(
        config,
        cot_transport=cot_transport,
        cot_group=args.cot_group,
        cot_port=args.cot_port,
        cot_stale_s=args.cot_stale,
    )
    group, port = bridge.gateway.endpoint
    await bridge.start()
    print(
        f"[atak-bridge] JDSS '{config.network.network_id}' {group}:{port} "
        f"<-> {cot_desc} — bridging (stale {args.cot_stale}s). Ctrl-C to stop."
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)
    try:
        while not stop.is_set():
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=5.0)
            s = bridge.stats
            print(
                f"[atak-bridge] CoT→JDSS {s['cot_in']}/{s['jdss_out']} · "
                f"JDSS→CoT {s['jdss_in']}/{s['cot_out']} · "
                f"stale-dropped {s['stale_dropped']} · deletes {s['cot_deleted']}"
            )
    finally:
        await bridge.stop()
        print("[atak-bridge] stopped")


async def _simulate(args: argparse.Namespace) -> None:
    from jdssarrow.simulator.scenario import Simulation

    sim = Simulation(
        network_id=args.network, transport=args.transport, codec=args.codec, rogue=args.rogue
    )
    extra = f" + 1 rogue ('{args.rogue}')" if args.rogue else ""
    print(
        f"Spawning {len(sim.clients)} JDSS-compliant clients{extra} on '{args.network}' "
        f"({args.codec}/{args.transport})…"
    )
    report = await sim.run(ticks=args.ticks, tick_interval=args.interval)
    print(report.format())
    if args.matrix:
        print("\nConnection matrix (messages accepted, observer ← originator):")
        print(report.format_matrix())
    compliant = report.all_seven_types and report.casevac_acks >= report.casevac_requests
    rogue_ok = args.rogue is None or report.rogue_rejected
    print("\nCOMPLIANCE:", "PASS ✓" if compliant and rogue_ok else "CHECK")


if __name__ == "__main__":
    main()
