#!/usr/bin/env python3
"""Generate the JDSSArrow documentation PDFs served by the web UI's Documentation tab.

The PDFs are treated as static build assets: they are written to ``web-ui/public/docs`` so
Vite bundles them into ``dist/docs`` and the FastAPI backend serves them at ``/docs/*.pdf``.

This is a *build-time* tool, not a runtime dependency of the app. Regenerate after editing the
content below with::

    uv pip install fpdf2        # one-off, generation only
    python docs/generate_docs.py

Requires: fpdf2 (https://py-pdf.github.io/fpdf2/).
"""

from __future__ import annotations

import datetime as _dt
from pathlib import Path

from fpdf import FPDF

OUT_DIR = Path(__file__).resolve().parents[1] / "web-ui" / "public" / "docs"

# The built-in PDF fonts are Latin-1 only; map the few non-ASCII glyphs we use to plain text so
# the documents render identically everywhere without shipping a Unicode TTF.
_SUBST = {
    "→": "->", "←": "<-", "↔": "<->", "⇒": "=>",
    "×": "x", "–": "-", "—": "--", "•": "-",
    "✓": "[x]", "✗": "x", "·": "-", "≤": "<=", "≥": ">=",
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "‹": "<", "›": ">", "\U0001f512": "[signed]", "…": "...",
}


def _san(text: str) -> str:
    for bad, good in _SUBST.items():
        text = text.replace(bad, good)
    return text.encode("latin-1", "replace").decode("latin-1")


# Palette (matches the dashboard's tactical dark-on-light accents).
INK = (28, 34, 43)
ACCENT = (43, 92, 154)
MUTED = (110, 122, 134)
RULE = (200, 208, 216)
CODE_BG = (243, 245, 248)
TABLE_HEAD = (43, 92, 154)


class Doc(FPDF):
    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__(format="A4", unit="mm")
        self.doc_title = title
        self.set_auto_page_break(auto=True, margin=18)
        self.set_margins(20, 20, 20)
        # Fixed metadata so regenerating byte-stable PDFs doesn't churn git history.
        self.set_creation_date(_dt.datetime(2026, 1, 1, tzinfo=_dt.timezone.utc))
        self.set_title(title)
        self.set_author("JDSSArrow")
        self._cover(title, subtitle)

    # --- chrome -----------------------------------------------------------------
    def header(self) -> None:
        if self.page_no() == 1:
            return
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 6, _san(self.doc_title), align="L")
        self.cell(0, 6, "JDSSArrow - STANAG 4677 / AEP-76", align="R")
        self.ln(7)
        self.set_draw_color(*RULE)
        self.line(20, self.get_y(), 190, self.get_y())
        self.ln(4)

    def footer(self) -> None:
        if self.page_no() == 1:
            return
        self.set_y(-15)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(*MUTED)
        self.cell(0, 8, f"Page {self.page_no() - 1}", align="C")

    def _cover(self, title: str, subtitle: str) -> None:
        self.add_page()
        self.ln(60)
        self.set_draw_color(*ACCENT)
        self.set_line_width(1.2)
        self.line(20, self.get_y(), 60, self.get_y())
        self.set_line_width(0.2)
        self.ln(10)
        self.set_font("Helvetica", "B", 30)
        self.set_text_color(*INK)
        self.multi_cell(0, 13, _san(title))
        self.ln(3)
        self.set_font("Helvetica", "", 13)
        self.set_text_color(*ACCENT)
        self.multi_cell(0, 7, _san(subtitle))
        self.ln(20)
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*MUTED)
        self.multi_cell(
            0, 6,
            _san(
                "JDSSArrow - a SOLID, pluggable reference implementation of the Joint "
                "Dismounted Soldier System (NATO STANAG 4677, specified in AEP-76, 5 volumes), "
                "with a web configuration and monitoring application.\n\n"
                "Reference / educational software implementing a published NATO "
                "interoperability standard. Not accredited or production tactical software."
            ),
        )

    # --- content blocks ---------------------------------------------------------
    def h1(self, text: str) -> None:
        self.add_page()
        self.set_font("Helvetica", "B", 20)
        self.set_text_color(*ACCENT)
        self.multi_cell(0, 10, _san(text))
        self.ln(1)
        self.set_draw_color(*ACCENT)
        self.line(20, self.get_y(), 190, self.get_y())
        self.ln(4)

    def h2(self, text: str) -> None:
        self._space(11)
        self.set_font("Helvetica", "B", 14)
        self.set_text_color(*INK)
        self.multi_cell(0, 8, _san(text))
        self.ln(1)

    def h3(self, text: str) -> None:
        self._space(9)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*ACCENT)
        self.multi_cell(0, 6, _san(text))
        self.ln(0.5)

    def para(self, text: str) -> None:
        self.set_font("Helvetica", "", 10)
        self.set_text_color(*INK)
        self.multi_cell(0, 5.6, _san(text))
        self.ln(2)

    def bullets(self, items: list[str]) -> None:
        self.set_font("Helvetica", "", 10)
        bullet_w = 5.0
        text_w = 170 - bullet_w
        for it in items:
            self._space(7)
            y0 = self.get_y()
            self.set_xy(20, y0)
            self.set_text_color(*ACCENT)
            self.cell(bullet_w, 5.6, _san("-"))
            self.set_text_color(*INK)
            self.set_xy(20 + bullet_w, y0)
            self.multi_cell(text_w, 5.6, _san(it))
        self.ln(2)

    def code(self, text: str) -> None:
        self.ln(1)
        self.set_font("Courier", "", 8.5)
        self.set_fill_color(*CODE_BG)
        self.set_text_color(*INK)
        self.set_draw_color(*RULE)
        pad = 3
        # Render each source line, soft-wrapping long lines to the box width.
        lines = _san(text).split("\n")
        line_h = 4.3
        usable = 170 - 2 * pad
        self.set_x(20)
        top = self.get_y()
        # Pre-measure height for the shaded box by wrapping each line.
        wrapped: list[str] = []
        for ln in lines:
            chunks = self.multi_cell(
                usable, line_h, ln if ln else " ", dry_run=True, output="LINES"
            )
            wrapped.extend(chunks or [" "])
        box_h = line_h * len(wrapped) + 2 * pad
        # page-break awareness
        if top + box_h > self.page_break_trigger:
            self.add_page()
            top = self.get_y()
        self.rect(20, top, 170, box_h, style="DF")
        self.set_xy(20 + pad, top + pad)
        for w in wrapped:
            self.set_x(20 + pad)
            self.cell(usable, line_h, w)
            self.ln(line_h)
        self.set_y(top + box_h)
        self.ln(3)

    def table(self, headers: list[str], rows: list[list[str]], widths: list[float]) -> None:
        self.ln(1)
        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(*TABLE_HEAD)
        self.set_text_color(255, 255, 255)
        self.set_draw_color(*RULE)
        self._space(14)
        for h, w in zip(headers, widths):
            self.cell(w, 7, _san(h), border=0, fill=True, align="L")
        self.ln(7)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*INK)
        fill = False
        for row in rows:
            # measure row height from the tallest wrapped cell
            heights = []
            for txt, w in zip(row, widths):
                chunks = self.multi_cell(w - 2, 5, _san(txt), dry_run=True, output="LINES") or [""]
                heights.append(len(chunks) * 5)
            rh = max(heights) + 2
            if self.get_y() + rh > self.page_break_trigger:
                self.add_page()
            self.set_fill_color(247, 249, 251) if fill else self.set_fill_color(255, 255, 255)
            y0 = self.get_y()
            x0 = self.get_x()
            x = x0
            for txt, w in zip(row, widths):
                self.rect(x, y0, w, rh, style="F")
                self.set_xy(x + 1, y0 + 1)
                self.multi_cell(w - 2, 5, _san(txt))
                x += w
                self.set_xy(x, y0)
            self.set_draw_color(*RULE)
            self.line(x0, y0 + rh, x0 + sum(widths), y0 + rh)
            self.set_xy(x0, y0 + rh)
            fill = not fill
        self.ln(3)

    def _space(self, needed: float) -> None:
        if self.get_y() + needed > self.page_break_trigger:
            self.add_page()


# ---------------------------------------------------------------------------
# Document content
# ---------------------------------------------------------------------------
def build_what_is_jdss() -> Doc:
    d = Doc("What is JDSS?", "The Joint Dismounted Soldier System - STANAG 4677 / AEP-76")

    d.h1("1. Overview")
    d.para(
        "The Joint Dismounted Soldier System (JDSS) is a NATO interoperability standard - "
        "STANAG 4677, technically specified in Allied Engineering Publication AEP-76 (Edition A, "
        "5 volumes). It provides soldier-level Command, Control, Communications and Computers (C4) "
        "interoperability at the tactical edge: the layer below HQ-level Friendly Force Tracking "
        "(NFFI / STANAG 5527)."
    )
    d.para(
        "National soldier systems - each built by a different nation with different radios, data "
        "formats and security - connect to a shared coalition network through a JDSS Gateway. The "
        "gateway composes the five AEP-76 volumes so that dismounted troops from different nations "
        "can exchange Command and Control (C2) data directly."
    )

    d.h2("The loaned radio concept")
    d.para(
        "The central idea of JDSS is the loaned radio: a coalition partner is given (loaned) a "
        "radio bearer that plugs their national soldier system into the coalition network. This "
        "enables direct C2 data exchange between coalition dismounted troops without every nation "
        "having to adopt the same national system - they only need to speak JDSS at the gateway."
    )

    d.h2("Where JDSS sits")
    d.bullets([
        "HQ / staff level: Friendly Force Tracking via NFFI (STANAG 5527) and JC3IEDM data.",
        "Soldier / tactical edge: JDSS (STANAG 4677 / AEP-76) - the focus of this standard.",
        "JDSS is deliberately the layer below FFT: it is about the individual dismounted "
        "soldier and small-team situational awareness, not the recognised air/maritime picture.",
    ])

    d.h1("2. The five AEP-76 volumes")
    d.para(
        "AEP-76 is organised into five volumes, each covering one concern of interoperability. A "
        "JDSS Gateway is precisely a composition of one implementation of each volume."
    )
    d.table(
        ["Vol", "AEP-76 subject", "What it defines"],
        [
            ["I", "Security", "Authenticity and integrity of every frame; classification "
             "marking and releasability caveats."],
            ["II", "Data Model (JDSSDM)", "The message content model - a MIP-3.1-XML variant, "
             "JC3IEDM-compliant, using APP-6(D) symbology."],
            ["III", "Loaned Radio", "The radio bearer abstraction that carries JDSS traffic over "
             "a tactical link."],
            ["IV", "Information Exchange Mechanism (JDSSIEM)", "How messages are framed, "
             "transported and reliably delivered between nodes."],
            ["V", "Network Access", "How a node obtains its addressing on the coalition network "
             "(e.g. deriving the multicast group)."],
        ],
        [14, 62, 94],
    )

    d.h1("3. The JDSS Data Model (JDSSDM)")
    d.para(
        "The JDSSDM (Volume II) is the shared language of the network. It is a MIP-3.1-XML "
        "variant, JC3IEDM-compliant, and uses APP-6(D) military symbology (Symbol Identification "
        "Codes) so that a contact reported by one nation renders as the correct symbol on every "
        "other nation's map."
    )
    d.para("Every JDSS message carries a header (originator, message id, reporting time, "
           "classification, releasability) and a typed body. There are seven message types:")
    d.table(
        ["Message type", "Purpose"],
        [
            ["Presence", "Periodic position/status beacon ('I am here') - drives the blue-force "
             "picture and peer liveness."],
            ["Identification", "Who a node is: callsign, unit, nation, role."],
            ["ContactSighting", "A report of an observed contact/target, with location and "
             "APP-6(D) symbol."],
            ["Sketch", "A free-form sketch (ordered points) - e.g. a route or a hasty diagram."],
            ["Overlay", "Tactical control measures / map graphics (boundaries, markers)."],
            ["CasevacRequest", "A casualty evacuation request (the 9-line), with urgency."],
            ["Chat", "Free-text tactical chat, optionally addressed to a recipient."],
        ],
        [40, 130],
    )

    d.h1("4. Security and classification")
    d.para(
        "Volume I makes every frame authenticated and integrity-protected. In this reference "
        "implementation the default security provider signs each payload with HMAC-SHA256 keyed "
        "by a coalition pre-shared key (PSK). A frame whose HMAC does not verify is dropped before "
        "it can reach the operational picture - this is how an unauthorised (wrong-key) node is "
        "kept off the network."
    )
    d.para("Every originated message is stamped with a classification level and a releasability "
           "caveat:")
    d.table(
        ["Level", "Marking", "Releasable-to example"],
        [
            ["0", "UNCLASSIFIED", "ALL"],
            ["1", "RESTRICTED", "REL BEL NLD"],
            ["2", "CONFIDENTIAL", "REL <nations>"],
            ["3", "SECRET", "REL <nations>"],
        ],
        [20, 60, 90],
    )
    d.para(
        "The receiving picture always shows the highest classification seen on the network, so an "
        "operator is never under-warned about the sensitivity of the data on their screen."
    )

    d.h1("5. Glossary")
    d.table(
        ["Term", "Meaning"],
        [
            ["JDSS", "Joint Dismounted Soldier System (STANAG 4677)."],
            ["AEP-76", "The Allied Engineering Publication specifying JDSS in 5 volumes."],
            ["JDSSDM", "JDSS Data Model (Volume II) - the message content model."],
            ["JDSSIEM", "JDSS Information Exchange Mechanism (Volume IV)."],
            ["Gateway", "The composition of the five volumes that joins a node to the network."],
            ["Loaned radio", "A radio bearer loaned to a partner to join the coalition network."],
            ["PSK", "Pre-shared key - the coalition secret keying HMAC authentication."],
            ["APP-6(D)", "NATO Joint Military Symbology (the symbol set used by JDSSDM)."],
            ["JC3IEDM", "Joint C3 Information Exchange Data Model that JDSSDM aligns with."],
            ["COP", "Common Operational Picture - the shared tactical picture."],
        ],
        [40, 130],
    )
    return d


def build_jdssarrow() -> Doc:
    d = Doc("JDSSArrow", "A SOLID, pluggable reference implementation of JDSS + web monitoring")

    d.h1("1. What JDSSArrow is")
    d.para(
        "JDSSArrow is a SOLID, scalable, pluggable reference implementation of the Joint "
        "Dismounted Soldier System (STANAG 4677 / AEP-76), together with a web configuration and "
        "monitoring application. It demonstrates the standard end to end: national nodes joining a "
        "coalition network, exchanging the seven JDSS message types, rejecting non-compliant "
        "traffic, and being observed and managed from a browser dashboard."
    )
    d.para(
        "It is reference / educational software implementing a published NATO interoperability "
        "standard - not accredited or production tactical software."
    )

    d.h1("2. Architecture")
    d.para(
        "Every AEP-76 volume depends only on abstractions (Python Protocols declared in "
        "interfaces.py). The gateway package is the composition root that wires concrete "
        "implementations together via dependency injection. Every seam is a runtime plugin, "
        "discovered through importlib.metadata entry points, so any piece can be swapped - even "
        "for a third-party implementation - without touching the core."
    )
    d.h3("Extension points (each a pluggable Protocol)")
    d.bullets([
        "Transport - how frames move between nodes (loopback, UDP multicast).",
        "Codec - how a JDSSDM message is encoded (xml, json, arrow).",
        "SecurityProvider - how a payload is authenticated (null, psk / HMAC-SHA256).",
        "RadioBearer - the loaned-radio abstraction (simulated).",
        "AddressAllocator - how the network address/multicast group is derived (default).",
        "ConnectionPolicy - the admit/deny matrix row (matrix, allow_all).",
        "MessageHandler, MetricsSink, ConfigStore - dispatch, telemetry and persistence seams.",
    ])

    d.h2("The five volumes map to modules")
    d.table(
        ["Vol", "AEP-76 subject", "Python module"],
        [
            ["I", "Security", "jdssarrow.security"],
            ["II", "Data Model (JDSSDM)", "jdssarrow.datamodel"],
            ["III", "Loaned Radio", "jdssarrow.loanedradio"],
            ["IV", "Information Exchange Mechanism", "jdssarrow.iem"],
            ["V", "Network Access", "jdssarrow.networkaccess"],
        ],
        [14, 76, 80],
    )
    d.para(
        "A node is a JdssGateway (the composed stack) driven by a SoldierNode (the high-level "
        "client API: start, identify, presence, report_contact, request_casevac, chat, stop)."
    )

    d.h1("3. The web application")
    d.para(
        "A FastAPI backend owns one gateway for its lifetime and exposes REST config endpoints, a "
        "monitoring snapshot, a Prometheus /metrics endpoint, a WebSocket that streams live "
        "message events, and an Apache Arrow IPC dump of recent telemetry. A React + TypeScript "
        "single-page app renders the dashboard. The backend also serves the built dashboard, so "
        "one process delivers both the API and the UI on port 8000."
    )
    d.h3("Dashboard tabs")
    d.bullets([
        "Dashboard - KPI strip (status, uptime, peers, messages, rejected, coalition version, "
        "classification), health, connected peers, the AEP-76 volumes, message injection and a "
        "live feed.",
        "Configuration - the hot-reloadable pluggable config editor, node identity/network, and "
        "the capability matrix (per-message-type receive/emit permissions).",
        "Connections & Policy - local connection management, the gossip-distributed coalition "
        "policy (with Ed25519-signed status), and the live connection matrix.",
        "Simulation - start/stop a live roster of JDSS-compliant clients on this node's network.",
        "Connect a Client - live join coordinates plus copy-paste onboarding for every client "
        "type.",
        "Logs - the application log and the per-message audit log, live.",
        "Documentation - links to these PDF guides.",
    ])

    d.h1("4. Interoperability simulator")
    d.para(
        "jdssarrow.simulator spawns a roster of role-based clients, each driving a real gateway, "
        "so every message is conformant by construction. Client types are themselves plugins "
        "(the jdssarrow.profiles entry-point group)."
    )
    d.table(
        ["Client", "Role", "Characteristic messages"],
        [
            ["rifleman", "dismounted soldier", "Presence, Chat"],
            ["teamleader", "team leader", "Identification, Presence, Chat, Contact, CASEVAC"],
            ["medic", "combat medic", "Presence, Chat; answers CASEVAC and repositions"],
            ["scout", "recce", "Contact, Sketch, Presence"],
            ["observer", "forward observer / JTAC", "Overlay (control measures), Contact"],
            ["sensor", "UAV / automated sensor", "Contact (detections), Presence"],
            ["commandpost", "HQ", "Identification, Chat (orders); builds the COP"],
            ["atak", "ATAK end-user device", "Presence, Chat, Contact, Overlay"],
            ["vehicle", "mounted C2 platform", "Identification, Presence, Contact"],
        ],
        [30, 55, 85],
    )

    d.h2("Rejecting non-compliant traffic")
    d.para(
        "A rogue client exercises each rejection boundary; the network drops its traffic while "
        "legitimate exchange is unaffected. Each rogue maps to a volume's defence:"
    )
    d.bullets([
        "wrong_key (Vol I) - unauthorised PSK, so the HMAC verify fails.",
        "garbage (Vol IV) - not a JDSS frame, so framing rejects it.",
        "insider (Vol II) - leaked key but a bad payload, so the codec/schema rejects it.",
    ])
    d.para(
        "Each node independently records who it accepted traffic from - one row of the N x N "
        "connection matrix. A rejected rogue's column is all-zero (nobody accepts it), even though "
        "its own row may be populated (it can still receive)."
    )

    d.h2("Managing connections and coalition policy")
    d.para(
        "Beyond observing, the matrix manages connections. Each node carries a connection policy "
        "(its row of the coalition admit/deny matrix), enforced on ingest after security and "
        "schema but before dispatch. For a network-wide rule, one node is designated the policy "
        "authority: it owns the coalition policy and distributes it over an HMAC-authenticated, "
        "versioned gossip channel; every node applies it under its own local policy (a peer must "
        "be allowed by both). Coalition updates are additionally signed with the authority's "
        "Ed25519 private key, so a coalition-key holder without the private key cannot forge or "
        "replay an update."
    )

    d.h1("5. Logging and observability")
    d.bullets([
        "Application log - standard Python logging on the jdssarrow logger (lifecycle, warnings, "
        "errors), captured into a bounded ring (GET /api/logs/app).",
        "Message audit log - one entry per message with direction (in/out), disposition "
        "(accepted/rejected) and, for rejections, the reason: framing, security, codec, policy, "
        "capability or duplicate (GET /api/logs/messages).",
        "Prometheus /metrics - counters that use the same reason tokens as the audit log, so "
        "metrics and the audit trail always agree.",
    ])

    d.h1("6. Technology stack")
    d.table(
        ["Layer", "Technology"],
        [
            ["Language / runtime", "Python 3.14 (asyncio)"],
            ["Web framework", "FastAPI + Uvicorn (ASGI)"],
            ["Data / validation", "Pydantic v2, pydantic-settings"],
            ["Serialisation", "lxml (XML), Apache Arrow (pyarrow) for telemetry"],
            ["Security", "cryptography (Ed25519), HMAC-SHA256"],
            ["Metrics", "prometheus-client"],
            ["Frontend", "React 18 + TypeScript, built with Vite"],
            ["Packaging", "uv + hatchling; container via Docker"],
        ],
        [55, 115],
    )

    d.h1("7. Quick start")
    d.code(
        "uv venv && uv pip install -e \".[dev]\"\n\n"
        "# run the test suite (incl. two-node loopback e2e round-trip)\n"
        "pytest -q\n\n"
        "# demo: two nodes exchange Presence + CASEVAC over real UDP multicast\n"
        "jdssarrow run --config examples/node-a.yaml   # terminal 1\n"
        "jdssarrow run --config examples/node-b.yaml   # terminal 2\n\n"
        "# interoperability simulator (prints a compliance report)\n"
        "jdssarrow simulate\n\n"
        "# web config + monitoring backend  ->  http://localhost:8000\n"
        "uvicorn jdssarrow.web.app:app --reload\n\n"
        "# or run the whole thing in Docker\n"
        "./deploy.sh"
    )
    return d


def build_how_to_config() -> Doc:
    d = Doc("How to Configure JDSSArrow", "The GatewayConfig model, config files, env vars and runtime edits")

    d.h1("1. The configuration model")
    d.para(
        "A single GatewayConfig object fully describes a node: its identity, which plugin "
        "implements each extension point, and the network parameters. Because every pluggable "
        "choice is just a string naming a registry entry (codec: \"xml\", transport: \"udp\"), "
        "reconfiguring the system - even swapping in a third-party plugin - never requires a code "
        "change."
    )
    d.h3("Layering (lowest to highest precedence)")
    d.bullets([
        "Field defaults - a bare GatewayConfig() is already runnable.",
        "Config file - a YAML or TOML file overlaid on the defaults.",
        "Environment variables - JDSS_-prefixed vars override the file.",
        "Runtime overrides - edits pushed from the web UI (PUT /api/config).",
    ])
    d.para(
        "The config file is chosen by the JDSS_CONFIG environment variable (or the --config flag "
        "on the CLI). With no file, the built-in defaults are used - so the app starts with zero "
        "configuration."
    )

    d.h1("2. A fully annotated config file")
    d.para("Every section below is optional; omit a section to accept its defaults. This example "
           "shows all of them (YAML; TOML is also supported).")
    d.code(
        "# node-a.yaml - a coalition node (nation ALFA), also the policy authority\n"
        "identity:\n"
        "  node_id: node-a            # unique id on the network\n"
        "  callsign: ALFA-1\n"
        "  unit: 1PL/A/1-501\n"
        "  nation: BEL\n"
        "  role: team_leader          # rifleman | team_leader | medic | scout | ...\n\n"
        "plugins:                     # each value names a registered plugin\n"
        "  codec: xml                 # Vol II - xml | json | arrow\n"
        "  transport: udp             # Vol IV - udp | loopback\n"
        "  security: psk              # Vol I  - psk (HMAC-SHA256) | null\n"
        "  bearer: simulated          # Vol III\n"
        "  allocator: default         # Vol V  - derives the multicast group from network_id\n\n"
        "network:\n"
        "  network_id: coalition-alpha\n"
        "  multicast_group:           # optional; blank => derived by the allocator\n"
        "  multicast_port:            # optional; blank => derived\n"
        "  repeat: 3                  # send each frame N times (1-8) for reliability\n"
        "  psk: shared-coalition-secret   # coalition pre-shared key - must match every node\n\n"
        "classification:\n"
        "  level: 1                   # 0=UNCLASSIFIED 1=RESTRICTED 2=CONFIDENTIAL 3=SECRET\n"
        "  releasable_to: REL BEL NLD\n\n"
        "connections:\n"
        "  policy: matrix             # matrix (manageable) | allow_all\n"
        "  default_action: allow      # allow | deny\n"
        "  blocked: []                # per-peer local blocks\n"
        "  allowed: []\n"
        "  policy_authority: node-a   # this node owns the coalition-wide policy\n"
        "  coalition_default_action: allow\n"
        "  coalition_blocked: []\n"
        "  authority_private_key: <hex>   # ONLY on the authority - signs updates (Ed25519)\n"
        "  authority_public_key: <hex>    # on EVERY node - verifies updates\n\n"
        "capabilities:                # per-message-type permissions (empty => all allowed)\n"
        "  receive: {}                # e.g. { Sketch: false } drops inbound Sketch\n"
        "  emit: {}                   # e.g. { CasevacRequest: false } forbids originating it\n\n"
        "gossip:\n"
        "  enabled: true              # broadcast this node's matrix row for the live matrix\n"
        "  interval_s: 2.0\n\n"
        "web:\n"
        "  host: 0.0.0.0\n"
        "  port: 8000\n"
        "  telemetry_capacity: 1000\n"
        "  peer_timeout_s: 15         # a peer is 'connected' if seen within this many seconds\n\n"
        "servers:                     # CoT/TAK servers to bridge to (CRUD from the UI)\n"
        "  - id: ots                  # stable id (auto-generated when added from the UI)\n"
        "    name: OpenTAKServer\n"
        "    host: 192.168.0.202\n"
        "    port: 8088               # 8088 plaintext, or 8089 with tls: true\n"
        "    tls: false\n"
        "    verify: true             # verify server cert (TLS only)\n"
        "    client_cert:             # PEM path for mutual TLS (CN = a registered OTS user)\n"
        "    client_key:\n"
        "    cacert:\n"
        "    enabled: true            # enabled entries connect live and bridge JDSS <-> CoT\n"
    )

    d.h1("3. Section reference")
    d.table(
        ["Section", "Key fields (defaults)", "Notes"],
        [
            ["identity", "node_id, callsign, unit, nation, role",
             "Who this node is; stamped on every originated message."],
            ["plugins", "codec=xml, transport=udp, security=psk, bearer=simulated, "
             "allocator=default", "Each value must name a registered plugin."],
            ["network", "network_id=default, repeat=2, psk=..., multicast_group/port=derived",
             "PSK and network_id must match across the coalition."],
            ["classification", "level=0, releasable_to=ALL",
             "Default marking; level 0-3."],
            ["connections", "policy=matrix, default_action=allow, policy_authority=None",
             "Admit/deny matrix and coalition policy authority + Ed25519 keys."],
            ["capabilities", "receive={}, emit={}",
             "Per-type on/off; empty means fully permissive."],
            ["gossip", "enabled=true, interval_s=2.0",
             "Peer-digest gossip feeding the live connection matrix."],
            ["web", "host=0.0.0.0, port=8000, peer_timeout_s=15",
             "Backend bind + monitoring parameters."],
            ["servers", "list of {id, name, host, port, tls, verify, certs, enabled}",
             "CoT/TAK servers to bridge to; CRUD from the Configuration tab, applied live."],
        ],
        [30, 80, 60],
    )

    d.h1("4. Environment variable overrides")
    d.para(
        "Every field can be overridden with a JDSS_-prefixed environment variable. Nested fields "
        "use a double-underscore delimiter (env_nested_delimiter). This is the most convenient way "
        "to tweak a container without editing a file."
    )
    d.code(
        "# override nested fields: JDSS_<SECTION>__<FIELD>\n"
        "export JDSS_IDENTITY__CALLSIGN=BRAVO-2\n"
        "export JDSS_NETWORK__NETWORK_ID=coalition-bravo\n"
        "export JDSS_NETWORK__PSK=another-shared-secret\n"
        "export JDSS_CLASSIFICATION__LEVEL=2\n\n"
        "# point the app at a config file (CLI flag or env var)\n"
        "export JDSS_CONFIG=/config/node.yaml\n"
        "uvicorn jdssarrow.web.app:app"
    )

    d.h1("5. Runtime configuration from the web UI")
    d.para(
        "The Configuration tab edits the plugin selection, reliability (repeat) and classification "
        "live. Applying issues PUT /api/config, which hot-restarts the gateway on the new "
        "selection. If the app was started with a config file, the edit is also persisted back to "
        "that file (via the FileConfigStore); otherwise it is applied in memory only."
    )
    d.para(
        "The capability matrix (Configuration tab) toggles per-message-type receive/emit "
        "permissions live via GET/POST /api/capabilities. A disallowed inbound type is dropped "
        "with reason 'capability'; emitting a disallowed type raises CapabilityError (HTTP 403)."
    )

    d.h1("6. Coalition authority signing keys")
    d.para(
        "For a coalition-wide policy authority you need an Ed25519 keypair. Generate one and place "
        "the private key ONLY on the authority node; every node (including the authority) carries "
        "the public key."
    )
    d.code(
        "jdssarrow keygen\n"
        "# prints:\n"
        "#   authority_private_key: <hex>   # -> only the authority's config, under connections:\n"
        "#   authority_public_key:  <hex>   # -> every node's config, under connections:"
    )

    d.h1("7. Adding your own plugin")
    d.para(
        "Because plugins are resolved from importlib.metadata entry points, a third party ships a "
        "new codec/transport/security/policy/profile by registering an entry point in the matching "
        "group - no fork required. For example, in another package's pyproject.toml:"
    )
    d.code(
        "[project.entry-points.\"jdssarrow.codecs\"]\n"
        "cbor = \"my_pkg.cbor_codec:CborCodec\"\n\n"
        "# then simply select it:  plugins.codec: cbor"
    )

    d.h1("8. Configuring the Docker deployment")
    d.para(
        "The container runs with the built-in defaults unless you provide a config. The deploy "
        "script mounts a file read-only at /config/node.yaml and sets JDSS_CONFIG for you; you can "
        "also inject individual JDSS_ env vars."
    )
    d.code(
        "# mount a config file when deploying\n"
        "CONFIG=examples/node-a.yaml ./deploy.sh\n\n"
        "# change the published port\n"
        "PORT=9000 ./deploy.sh\n\n"
        "# or with docker directly\n"
        "docker run -d -p 8000:8000 \\\n"
        "  -v $PWD/examples/node-a.yaml:/config/node.yaml:ro \\\n"
        "  -e JDSS_CONFIG=/config/node.yaml \\\n"
        "  jdssarrow:latest"
    )
    return d


def build_how_to_connect() -> Doc:
    d = Doc("How to Connect a Client", "Every way to join a JDSS network - CLI, Python SDK, ATAK and raw wire")

    d.h1("1. The join contract")
    d.para(
        "To join a JDSS network a client must agree on four things with the running node: the "
        "network id, the multicast endpoint, the codec, and the security (PSK). The live values "
        "for the running node are returned by GET /api/connect and shown, ready to copy, in the "
        "Connect a Client tab of the dashboard."
    )
    d.table(
        ["Parameter", "Meaning"],
        [
            ["network_id", "Logical coalition network name; must match on every node."],
            ["multicast_group : port", "Where frames are sent; derived from network_id if not set."],
            ["codec", "Wire encoding of the JDSSDM message (xml | json | arrow)."],
            ["security + psk", "psk = HMAC-SHA256 over the payload, keyed by the coalition PSK."],
            ["repeat", "How many times each frame is sent for reliability (receivers dedup)."],
            ["policy_authority", "If set, the node owning the coalition admit/deny policy."],
        ],
        [46, 124],
    )
    d.para("There are four ways to connect a client. Pick the one that matches the client.")

    d.h1("2. Method 1 - National soldier system / CLI node")
    d.para(
        "The simplest path: drop a YAML config and run the bundled node. It joins over real UDP "
        "multicast and starts beaconing Presence."
    )
    d.code(
        "# node-x.yaml - a new client joining this JDSS network\n"
        "identity:\n"
        "  node_id: node-x\n"
        "  callsign: XRAY-9\n"
        "  nation: XXX\n"
        "  role: rifleman\n"
        "plugins:\n"
        "  codec: xml\n"
        "  transport: udp\n"
        "  security: psk\n"
        "network:\n"
        "  network_id: coalition-alpha    # must match the network you are joining\n"
        "  psk: shared-coalition-secret   # must match the coalition PSK\n"
        "  repeat: 3"
    )
    d.code(
        "# install, then run the node - it joins the multicast group and beacons\n"
        "uv pip install -e \".[dev]\"\n"
        "jdssarrow run --config node-x.yaml --casevac"
    )
    d.para("If the network has a policy authority, also add its public key so the node can verify "
           "coalition policy updates:")
    d.code(
        "connections:\n"
        "  policy_authority: node-a\n"
        "  authority_public_key: <authority Ed25519 pubkey hex>"
    )

    d.h1("3. Method 2 - Custom client via the Python SDK")
    d.para(
        "For a bespoke client, drive a gateway directly with the SoldierNode API. Build a "
        "GatewayConfig, start the node, then call the high-level message methods (identify, "
        "presence, report_contact, request_casevac, chat)."
    )
    d.code(
        "import asyncio\n"
        "from jdssarrow.config.models import (\n"
        "    GatewayConfig, NodeIdentity, PluginSelection, NetworkConfig,\n"
        ")\n"
        "from jdssarrow.gateway.gateway import JdssGateway\n"
        "from jdssarrow.gateway.node import SoldierNode\n\n"
        "cfg = GatewayConfig(\n"
        "    identity=NodeIdentity(node_id=\"node-x\", callsign=\"XRAY-9\"),\n"
        "    plugins=PluginSelection(codec=\"xml\", transport=\"udp\", security=\"psk\"),\n"
        "    network=NetworkConfig(network_id=\"coalition-alpha\",\n"
        "                          psk=\"shared-coalition-secret\"),\n"
        ")\n\n"
        "async def main():\n"
        "    node = SoldierNode(JdssGateway(cfg))\n"
        "    await node.start()\n"
        "    await node.identify()\n"
        "    await node.presence(50.85, 4.35, battery_pct=90)   # appears on every peer's COP\n"
        "    await node.report_contact(50.86, 4.36, \"dismounted patrol\")\n"
        "    await asyncio.sleep(2)\n"
        "    await node.stop()\n\n"
        "asyncio.run(main())"
    )

    d.h1("4. Method 3 - ATAK / Cursor-on-Target (via a bridge)")
    d.para(
        "ATAK speaks Cursor-on-Target (CoT) XML over the TAK protocol, not JDSSDM. A bridge runs a "
        "JDSS node and joins ATAK's bearer, translating both ways. The bundled bridge "
        "(jdssarrow.bridges.atak) supports either ATAK mesh SA multicast or a TAK Server over TCP "
        "with optional mutual TLS. It is loop-safe and manages stale tracks."
    )
    d.code(
        "# ATAK mesh SA (multicast 239.2.3.1:6969)\n"
        "jdssarrow bridge atak --config examples/node-c.yaml\n\n"
        "# ...or a TAK Server over TCP / mutual TLS\n"
        "jdssarrow bridge atak --config examples/node-c.yaml \\\n"
        "    --tak-server tak.example.mil:8089 --tak-tls \\\n"
        "    --tak-cert client.pem --tak-key client.key --tak-cacert ca.pem"
    )
    d.h3("CoT <-> JDSS mapping")
    d.table(
        ["Cursor-on-Target", "JDSS message"],
        [
            ["a-f-* (friendly)", "Presence"],
            ["a-h / a-u / a-n-* (hostile/unknown/neutral)", "ContactSighting (with APP-6(D) SIDC)"],
            ["GeoChat (b-t-f)", "Chat"],
            ["markers / drawings (u-d-*, b-m-p-*)", "Overlay graphics"],
            ["medevac 9-line (b-r-f-h-c)", "CasevacRequest"],
        ],
        [85, 85],
    )
    d.para(
        "A node's Presence maps to a stable CoT uid with a stale window, so ATAK keeps one moving "
        "icon and auto-expires it when beacons stop; the bridge also sweeps silent tracks (emitting "
        "a CoT delete) and drops already-stale inbound CoT. Point ATAK at the same bearer and its "
        "tracks appear on the coalition COP while coalition traffic appears on the EUD."
    )

    d.h3("Connect ATAK directly to JDSSArrow (built-in TAK server)")
    d.para(
        "JDSSArrow can itself act as the TAK server, so ATAK connects straight to this node with no "
        "OpenTAKServer or FreeTAKServer in the middle. In the web console open Configuration -> "
        "Serve ATAK / EUDs, set a port (default 8087) and click Start server. The node then listens "
        "for EUD connections and bridges CoT<->JDSS for each one; every connected device shows up as "
        "its own coalition peer."
    )
    d.bullets([
        "In ATAK: Settings -> Network Preferences -> Network Connections -> TAK Servers -> + and add "
        "this node's address and the port, as TCP (no SSL). The panel shows the exact address.",
        "The device must be able to reach the JDSSArrow host (same LAN, or routed/VPN).",
        "Under Docker/NAT the auto-detected address is the container's internal IP, which EUDs "
        "cannot reach. Set the 'Advertised host' field to the host's LAN IP (or a public IP/DNS), "
        "and publish the port from the container (deploy.sh publishes EUD_PORT, default 8087; or "
        "docker run -p 8087:8087). Open it on the firewall for off-LAN clients.",
        "Once connected, ATAK's position appears on the coalition COP and coalition traffic streams "
        "to the EUD as CoT; the panel lists connected EUDs live.",
    ])

    d.h3("Connecting to OpenTAKServer (OTS)")
    d.para(
        "The same bridge connects to OpenTAKServer, which aggregates EUDs over a TCP/SSL stream of "
        "CoT XML. OTS accepts legacy XML CoT (TAK protocol version 0), which is exactly what the "
        "bridge speaks - no protobuf negotiation is required. Two paths work:"
    )
    d.table(
        ["OTS port", "Auth", "How the bridge connects"],
        [
            ["8088 (TCP, plaintext)", "anonymous",
             "Simplest. OTS relays CoT from a plain TCP client with no authentication."],
            ["8089 (SSL streaming)", "client certificate",
             "Present a client cert whose CommonName is a registered OTS user; OTS authenticates "
             "it by cert alone (no username/password message needed)."],
        ],
        [46, 40, 84],
    )
    d.code(
        "# 1) Plaintext TCP on OpenTAKServer's port 8088 (anonymous)\n"
        "jdssarrow bridge atak --config examples/node-c.yaml \\\n"
        "    --tak-server ots.example.org:8088\n\n"
        "# 2) SSL on port 8089 with a client cert enrolled for an OTS user\n"
        "#    (export the .p12 from OTS and split it into PEMs, or use your enrollment certs)\n"
        "jdssarrow bridge atak --config examples/node-c.yaml \\\n"
        "    --tak-server ots.example.org:8089 --tak-tls \\\n"
        "    --tak-cert ots-client.pem --tak-key ots-client.key --tak-cacert ots-ca.pem"
    )
    d.bullets([
        "OTS's SSL port requires authentication: a client cert whose CN names a known OTS user "
        "authenticates by cert; an unknown/unenrolled cert connects but its CoT is ignored.",
        "The bridge's HMAC/PSK security is JDSS-side only; the CoT carried to OTS is standard XML, "
        "so ATAK/WinTAK/iTAK clients on the same OTS see the coalition tracks and vice versa.",
        "Verified against OpenTAKServer's own EUD-handler framing and cert-auth logic in the test "
        "suite (tests/test_opentakserver_interop.py).",
    ])

    d.h3("From the web console - no CLI")
    d.para(
        "The Configuration tab has a CoT/TAK Server Connections panel that manages these "
        "connections (CRUD) from the browser, with live status. To add OpenTAKServer:"
    )
    d.bullets([
        "Plaintext: add a server with host + port 8088, TLS off - it connects immediately.",
        "TLS (8089): tick TLS and import a .p12 / .pfx (client cert + key + CA) with its password "
        "right in the browser. The backend decrypts it (POST /api/servers/pkcs12) into PEM material "
        "stored on the connection - no file paths, no shell. The cert's CommonName must be a "
        "registered OTS user.",
        "Use Test connection to probe before saving; edits and enable/disable apply live and "
        "persist to the config file.",
    ])

    d.h1("5. Method 4 - Any language, raw wire format")
    d.para(
        "Any language can join by producing the JDSS frame and authentication directly. The frame "
        "layout is:"
    )
    d.code(
        "frame  = magic(\"JDSS\",4) | version(1) | codec-name | security-name | len(4, big-endian) | payload\n"
        "payload = security.protect( codec.encode(JdssMessage) )\n\n"
        "Steps:\n"
        "  1. build a JDSSDM message (header + typed body) and encode it with the chosen codec\n"
        "  2. append/verify HMAC-SHA256(payload, key = coalition PSK)   [security = \"psk\"]\n"
        "  3. send the frame to the UDP multicast group:port (send it `repeat` times)\n"
        "  4. receivers dedup by (originator_id, message_id)"
    )
    d.para("The seven message types available on the network are listed below.")
    d.table(
        ["Message type", "Body summary"],
        [
            ["Presence", "position (lat/lon), status/battery"],
            ["Identification", "callsign, unit, nation, role"],
            ["ContactSighting", "observed location + APP-6(D) symbol id"],
            ["Sketch", "ordered list of points"],
            ["Overlay", "list of graphic control measures"],
            ["CasevacRequest", "casualty location + urgency (9-line)"],
            ["Chat", "free text + optional recipient"],
        ],
        [45, 125],
    )

    d.h1("6. Why a client might be rejected")
    d.para(
        "A joining client's traffic is validated on ingest. If it is dropped, the reason is one of "
        "the audit tokens below (visible in the Logs tab and in Prometheus counters). Checks run "
        "in order: framing, then security, then codec/schema, then policy, then capability."
    )
    d.table(
        ["Reason", "Cause / fix"],
        [
            ["framing", "Not a valid JDSS frame (bad magic/length). Check the wire format."],
            ["security", "HMAC verify failed. The PSK does not match the coalition PSK."],
            ["codec", "Payload did not decode/validate against JDSSDM. Check the codec + schema."],
            ["policy", "The peer is blocked by local or coalition admit/deny policy."],
            ["capability", "The message type is disabled for receive on this node."],
            ["duplicate", "A repeat of an already-seen (originator_id, message_id) - expected."],
        ],
        [34, 136],
    )

    d.h1("7. Verifying the connection")
    d.bullets([
        "Dashboard -> Connected Peers: the new node appears with its callsign, nation and role.",
        "Dashboard -> Live Message Feed: its Presence/Identification stream in.",
        "Connections & Policy -> Connection Matrix: its column fills in as nodes accept it "
        "(a rejected node's column stays all-zero).",
        "Logs -> Message Log: filter by disposition = rejected to see any drop reasons.",
    ])
    return d


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    docs = {
        "what-is-jdss.pdf": build_what_is_jdss,
        "jdssarrow.pdf": build_jdssarrow,
        "how-to-config.pdf": build_how_to_config,
        "how-to-connect-clients.pdf": build_how_to_connect,
    }
    for filename, builder in docs.items():
        doc = builder()
        out = OUT_DIR / filename
        doc.output(str(out))
        print(f"wrote {out.relative_to(OUT_DIR.parents[2])}  ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
