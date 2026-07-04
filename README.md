# JDSSArrow

A **SOLID, scalable, pluggable** reference implementation of the **Joint Dismounted Soldier System
(JDSS)** — NATO **STANAG 4677**, technically specified in **AEP-76** (5 volumes, Ed A) — plus a
**web config + monitoring** application.

JDSS provides *soldier-level* C4 interoperability at the tactical edge (the layer below
NFFI/STANAG 5527 HQ-level FFT). National soldier systems connect to a coalition network through a
**JDSS Gateway** that composes the five AEP-76 volumes. The central concept is the **loaned
radio** — direct C2 data exchange between coalition dismounted troops.

## The five volumes → modules

| Vol | AEP-76 subject | Module |
|-----|----------------|--------|
| I   | Security | [`jdssarrow.security`](src/jdssarrow/security) |
| II  | Data Model (JDSSDM) | [`jdssarrow.datamodel`](src/jdssarrow/datamodel) |
| III | Loaned Radio | [`jdssarrow.loanedradio`](src/jdssarrow/loanedradio) |
| IV  | Information Exchange Mechanism (JDSSIEM) | [`jdssarrow.iem`](src/jdssarrow/iem) |
| V   | Network Access | [`jdssarrow.networkaccess`](src/jdssarrow/networkaccess) |

The **JDSSDM** is a MIP-3.1-XML variant (JC3IEDM-compliant) using **APP-6(D)** symbology. Message
types: Presence, Identification, Contact/Sighting, Sketch, Overlay, CASEVAC Request, Chat.

## Architecture

Every volume depends only on abstractions (`Protocol`s in `interfaces.py`). The `gateway` package is
the **composition root** wiring concrete implementations via dependency injection. Extension points
(`Transport`, `Codec`, `SecurityProvider`, `RadioBearer`, `AddressAllocator`, `MessageHandler`,
`MetricsSink`, `ConfigStore`) are discovered as plugins via `importlib.metadata` entry points, so any
piece can be swapped without touching the core.

## Quick start

```bash
uv venv && uv pip install -e ".[dev]"

# run the test suite (incl. two-node loopback e2e round-trip)
pytest -q

# demo: two nodes exchange Presence + CASEVAC over real UDP multicast
jdssarrow run --config examples/node-a.yaml   # terminal 1
jdssarrow run --config examples/node-b.yaml   # terminal 2

# interoperability simulator: a roster of JDSS-compliant clients on one coalition net
jdssarrow simulate                            # loopback, prints a compliance report
jdssarrow simulate --transport udp --ticks 30 # over real UDP multicast

# web config + monitoring backend
uvicorn jdssarrow.web.app:app --reload        # http://localhost:8000
curl localhost:8000/metrics                   # Prometheus counters

# React dashboard (needs Node.js)
cd web-ui && npm install && npm run dev
```

## Simulator — JDSS-compliant client types

`jdssarrow.simulator` spawns a roster of role-based clients, each driving a real gateway, so
every message is conformant by construction. Client types are pluggable (`jdssarrow.profiles`
entry-point group):

| Client | Role | Characteristic messages |
|--------|------|-------------------------|
| `rifleman` | dismounted soldier | Presence, Chat |
| `teamleader` | team leader | Identification, Presence, Chat, Contact, **CASEVAC** |
| `medic` | combat medic | Presence, Chat — *answers CASEVAC and repositions* |
| `scout` | recce | Contact, **Sketch**, Presence |
| `observer` | forward observer / JTAC | **Overlay** (control measures), Contact, Presence |
| `sensor` | UAV / automated sensor | Contact (detections), Presence |
| `commandpost` | HQ | Identification, Chat (orders) — *builds the common operational picture* |
| `atak` | ATAK end-user device | Presence, Chat, Contact, **Overlay** (map markers) |
| `vehicle` | mounted C2 platform | Identification, Presence, Contact (higher mobility) |

Each profile also declares a **device class** (`eud`, `atak`, `uav`, `tablet`, `vehicle`,
`c2_workstation`) shown in the Simulation tab.

Add your own client type by subclassing `ClientProfile` and registering it in the
`jdssarrow.profiles` group (see `test_simulator.py::test_custom_client_type_plugs_in`).

### Rejecting non-compliant traffic

A **rogue** client exercises each rejection boundary — the network drops its traffic and it
never reaches the common operational picture, while legitimate exchange is unaffected:

```bash
jdssarrow simulate --rogue wrong_key   # Vol I: unauthorised PSK  → HMAC verify fails
jdssarrow simulate --rogue garbage     # Vol IV: not a JDSS frame → framing rejects
jdssarrow simulate --rogue insider     # Vol II: leaked key, bad payload → codec rejects
```

### Connection matrix

Each node independently records who it *accepted* traffic from (`GatewayMetrics.peers()` — one
row of the matrix). The simulator assembles the full N×N grid; a rejected rogue's **column is
all-zero** (nobody accepts it) even though its row is populated (it can still receive):

```bash
jdssarrow simulate --matrix --rogue garbage    # prints the ASCII matrix
```

In the web dashboard the **Connection Matrix** panel shows the same grid in two modes:

- **Live** (`GET /api/monitor/matrix`) — the real cross-node matrix, assembled from
  **peer-digest gossip**: every node periodically broadcasts the row it can see as an
  out-of-band, HMAC-protected control frame (`ExchangeEngine.publish_control`), and each node
  collects the rows it receives (`monitor/gossip.py`). Digests never enter the operational
  picture, and an unauthorised node's digest is dropped like its data — so a rejected node
  never appears as a column. Enabled per node via `gossip:` in the config.
- **Probe** (`GET /api/monitor/matrix/probe?rogue=…`) — a deterministic in-process exercise
  for demonstrating rejection on demand.

### Managing connections (not just observing)

The matrix also *manages* connections. Each node carries a **connection policy** — its row of
the coalition admit/deny matrix (`connections/policy.py`, pluggable `jdssarrow.policies`):

- `MatrixConnectionPolicy` (default) — a default action plus per-peer overrides, editable at
  runtime; `AllowAllPolicy` for open exercises. Configure via `connections:` in the node config.
- Enforced on ingest in the IEM (`ExchangeEngine`, drop reason `policy`), *after* security and
  schema but *before* dispatch — a blocked peer never enters the operational picture.
- Manage it live: `GET /api/connections`, `POST /api/connections/{peer}?action=block|allow|reset`,
  or the **Connection Management** dashboard panel. Blocked peers are marked `✗` in the matrix.

```bash
# simulator demo: the command post refuses the UAV sensor; everyone else still hears it
python -c "import asyncio; from jdssarrow.simulator.scenario import Simulation; \
print(asyncio.run(Simulation(blocks={'commandpost-1':['sensor-1']}).run(16)).format_matrix())"
```

#### Coalition-wide policy (centralized, gossip-distributed)

Local policy is per-node. For a **network-wide** admit/deny rule, designate a **policy
authority** (`connections.policy_authority: <node_id>`). The authority owns the coalition
policy and distributes it over the gossip control channel (`connections/distributor.py`,
versioned + HMAC-authenticated); every node applies it under its local policy via
`CompositePolicy` (a peer must be allowed by **both**). So blocking a node at the authority
blocks it across the whole coalition.

- `GET /api/coalition`, `POST /api/coalition/{peer}?action=block|allow|reset` (authority-only,
  else `403`), and the **Coalition Policy** dashboard panel.
- Example configs designate `node-a` as the authority; the others trust it. Blocking `node-c`
  at node-a makes node-b/node-d (separate processes) stop accepting node-c within a gossip cycle.

**Per-authority signing.** Updates are additionally signed with the authority's own **Ed25519**
private key (`connections/signing.py`); every node verifies with the authority's public key
(`connections.authority_public_key`). A coalition-key holder without the private key cannot forge
an update — signed payloads cover the version + policy, so replays/tampering fail too. Generate a
keypair with `jdssarrow keygen`; put the private key only on the authority.

## Logging

Two streams (`audit.py`):

- **Application log** — standard Python `logging` on the `jdssarrow` logger (lifecycle, warnings,
  errors), captured into a bounded ring for the dashboard (`GET /api/logs/app`).
- **Message audit log** — one entry per message the node handles: `direction` (in/out),
  `disposition` (accepted/rejected) and, for rejections, the **reason why** — `framing`,
  `security`, `codec`, `policy`, `capability` or `duplicate` (`GET /api/logs/messages`,
  filterable by direction/disposition). The reasons are the same tokens as the drop counters, so
  the audit log and Prometheus metrics always agree.

The **Logs** dashboard tab shows both, live.

## Web dashboard

The React SPA is organised into tabs:

- **Dashboard** — KPI strip (status, uptime, peers, messages, rejected, coalition version,
  classification) plus health, connected peers, volumes, message injection and the live feed.
- **Configuration** — the hot-reloadable pluggable config editor, node identity/network, a
  **capability matrix** (per-message-type receive/emit on-off permissions; `GET/POST
  /api/capabilities`, enforced in the IEM — inbound disallowed types dropped with reason
  `capability`, emit of a disallowed type raises `CapabilityError` → HTTP 403), and
  **CoT/TAK server connections** — full CRUD (`GET/POST/PUT/DELETE /api/servers`,
  `POST /api/servers/test`) over the servers this node bridges to (e.g. OpenTAKServer). Each
  enabled entry drives a live, auto-reconnecting connector that relays coalition JDSS traffic out
  as CoT and inbound CoT back onto the network; edits apply immediately and persist to the config
  file (`servers:` section). A **built-in ATAK/EUD TAK server** (`GET/PUT /api/eud`,
  `eud_server:` section) can also be toggled on so ATAK/WinTAK/iTAK devices connect *directly* to
  this node — it listens on a TCP port and translates CoT↔JDSS per connected EUD (each EUD shows
  up as its own coalition peer), so no separate TAK server is needed.
- **Connections & Policy** — local connection management, the gossip-distributed **coalition
  policy** (with `🔏 Ed25519-signed` status), and the live connection matrix.
- **Simulation** — **start/stop a live simulation** (`SimulationManager`, `GET/POST /api/sim`).
  By default the roster joins this node's own network/PSK/transport, so starting it makes the
  whole dashboard (peers, feed, matrix, coalition) come alive; options include tick interval,
  a rogue client, and an isolated (loopback) mode.
- **Connect a Client** — live join coordinates for *this* network (`GET /api/connect`) plus
  copy-paste onboarding for every client type: a national **CLI node** (YAML + command), the
  **Python SDK**, an **ATAK / Cursor-on-Target** bridge contract, and the **raw wire format**
  for any language.


## Connecting ATAK (Cursor-on-Target)

ATAK speaks CoT, not JDSSDM, so a **bridge** translates between them. `jdssarrow.bridges.atak`
runs a JDSS node and joins ATAK's CoT multicast group, relaying both ways:

```bash
# ATAK mesh SA (multicast 239.2.3.1:6969)
jdssarrow bridge atak --config examples/node-c.yaml

# ...or a TAK Server over TCP / mutual TLS
jdssarrow bridge atak --config examples/node-c.yaml \
    --tak-server tak.example.mil:8089 --tak-tls \
    --tak-cert client.pem --tak-key client.key --tak-cacert ca.pem
```

### OpenTAKServer

The TAK Server connector interoperates with **OpenTAKServer (OTS)**, which streams legacy XML CoT
(TAK protocol version 0) — exactly what the bridge speaks, so no protobuf negotiation is needed.
Two paths work (verified in `tests/test_opentakserver_interop.py`, which reproduces OTS's own
EUD-handler framing and cert-auth logic):

```bash
# OTS plaintext TCP streaming port (8088) — anonymous, no auth
jdssarrow bridge atak --config examples/node-c.yaml --tak-server ots.example.org:8088

# OTS SSL streaming port (8089) — client cert whose CN is a registered OTS user
jdssarrow bridge atak --config examples/node-c.yaml \
    --tak-server ots.example.org:8089 --tak-tls \
    --tak-cert ots-client.pem --tak-key ots-client.key --tak-cacert ots-ca.pem
```

OTS's SSL port **requires authentication**: a client certificate whose CommonName names a known
OTS user is authenticated by cert alone (no `<auth>` username/password message is sent), while an
unenrolled cert connects but has its CoT ignored. The plaintext **8088** port is anonymous. Note
OTS uses **8088** for TCP (the bridge's non-TLS default of `8087` is the FreeTAKServer/official-TAK
convention), so pass `:8088` explicitly.

**From the web console (no CLI).** The **Configuration → CoT/TAK Server Connections** panel does the
same thing without a shell: add a server (host `192.168.0.202`, port `8088`, TLS off) and it
connects live. For TLS (8089), tick **TLS** and **import a `.p12`/`.pfx`** (client cert + key + CA)
with its password directly in the browser — `POST /api/servers/pkcs12` decrypts it into PEM
material (the connection stores the PEMs, so no file paths are involved). The imported certificate's
CommonName must be a registered OTS user.

Mapping (`bridges/cot.py`): CoT `a-f-*` ↔ Presence, `a-h/-u/-n-*` ↔ ContactSighting, GeoChat
`b-t-f` ↔ Chat, `u-d-*`/`b-m-p-*` ↔ Overlay, medevac `b-r-f-h-c` ↔ CasevacRequest. Loop-safe
(own echoes carry a `__jdssbridge` marker; the bridge never re-emits its own traffic).

- **Bearers**: ATAK **mesh SA** multicast *or* a **TAK Server** TCP stream with optional
  mutual TLS (`bridges/takserver.py`), selected by `--tak-server`. The TAK Server connector
  **auto-reconnects** with exponential backoff and holds outbound CoT in a **bounded queue**
  (drop-oldest when full) that is **flushed in order on reconnect**, so a brief link drop
  doesn't lose traffic.
- **Stale-track handling**: a node's Presence maps to a **stable CoT uid** with a `--cot-stale`
  window, so ATAK maintains one moving icon and **auto-expires** it when beacons stop; the
  bridge also **sweeps** silent tracks and emits a CoT delete (`t-x-d-d`), and **drops
  already-stale inbound CoT** rather than injecting dead tracks.

Point ATAK at the same bearer and its tracks appear on the coalition COP, and coalition traffic
appears on the EUD. The **Connect a Client → ③ ATAK** tab shows the live join coordinates.

## Layout

See [the plan](.) and `src/jdssarrow/` — one sub-package per AEP-76 volume, plus `gateway/`,
`monitor/`, `web/`, `config/`, `plugins/`.

> Reference/educational software implementing a published NATO interoperability standard. Not
> accredited or production tactical software.
