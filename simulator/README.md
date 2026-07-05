# JDSS External Simulator (PyQt6)

A standalone desktop app that connects to a **JDSS gateway** as a set of coalition units and drives
tactical **scenarios in a loop**, streaming the full JDSS message set (Presence with course/speed,
Identification, ContactSighting, CasevacRequest, Chat, Overlay). It can connect **secure**
(PSK / HMAC‑SHA256) or **non‑secure** (null), and shows every message going out and coming in.

## Scenarios

| Key | Scenario |
|-----|----------|
| `eben_emael` | **Airborne assault on Fort Eben‑Emael** (10 May 1940) — glider assault teams on the fort plus the Albert Canal bridges (Vroenhoven, Veldwezelt, Kanne). |
| `narvik` | **Beach landing at Narvik** (1940) — Allied amphibious landings (Bjerkvik, Ankenes, Øyjord) with naval gunfire in the Ofotfjord. |

Each unit patrols a **closed route**, so the simulation loops continuously. Coordinates are
approximate/illustrative.

## Install & run

```bash
uv pip install PyQt6            # the app's only extra dependency
python -m simulator            # from the repo root
```

In the app: pick a **Scenario** and **Mode**, then **Start**. The unit table shows live positions;
the log tabs show the traffic.

### Modes

- **Inject into gateway (HTTP)** — *default, recommended.* The simulator **injects** each message
  into the running gateway via `POST /api/inject`, and the **gateway fans it out** to all of its
  connected clients — ATAK EUDs, TAK servers (OpenTAKServer), the dashboard, and coalition multicast
  peers. Set only the **Gateway URL** (default `http://localhost:8000`). This is the correct model
  ("clients receive because the gateway relays"), and it **works through Docker/NAT** (plain HTTP),
  unlike UDP multicast. Each unit shows up as its own coalition peer with its APP‑6(D) symbol.

- **Coalition multicast (UDP)** — the simulator joins the coalition network directly as peer nodes.
  Match the gateway's `network_id`, `psk`, `codec` and security mode (secure↔`psk`,
  non‑secure↔`null`). Note: UDP multicast **does not cross a Docker bridge network**, so this mode
  needs the gateway running natively on the same host/LAN.

## Architecture

- `geo.py` — great‑circle movement helpers.
- `scenarios.py` — the two scenarios (units, routes, enemies, orders) as plain data.
- `engine.py` — `SimulatorEngine`: builds one real `JdssGateway` per unit, joins the network, and
  loops (advance → emit). UI‑agnostic (pushes dicts to an `on_event` callback), so it runs headless
  and under pytest.
- `gui.py` — the PyQt6 window; the engine runs in a `QThread` and reports via a Qt signal.

Tests: `tests/test_simulator_app.py` exercises the engine over loopback (both scenarios, secure and
non‑secure, movement, and receiving external gateway traffic) — no display required.
