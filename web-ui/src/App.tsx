import { type CSSProperties, type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  CLASS_LABELS,
  CLASS_SHORT,
  Capabilities,
  classColor,
  CoalitionPolicy,
  AppLogRecord,
  ConnectInfo,
  ConnMatrix,
  Connections,
  MsgLogEntry,
  EventStream,
  GatewayConfig,
  Health,
  MessageEvent,
  openEventStream,
  Peer,
  SimStatus,
  Snapshot,
} from "./api";

const VOLUME_MODULES: Record<string, string> = {
  I: "security",
  II: "datamodel",
  III: "loanedradio",
  IV: "iem",
  V: "networkaccess",
};

// distinct colour per JDSS message type (drives feed border + dot)
const TYPE_COLOR: Record<string, string> = {
  Presence: "#3a78c2",
  Identification: "#8e7cc3",
  ContactSighting: "#c0392b",
  CasevacRequest: "#e08a1e",
  Chat: "#3aa76d",
  Sketch: "#4aa0a0",
  Overlay: "#a0a04a",
};
const typeColor = (t?: string) => (t && TYPE_COLOR[t]) || "#7c8a99";

function ClassBadge({ level }: { level: number }) {
  return (
    <span className="cbadge" style={{ background: classColor(level) }} title={CLASS_LABELS[level]}>
      {CLASS_SHORT[level] ?? "?"}
    </span>
  );
}

type Tab = "dashboard" | "config" | "connections" | "simulation" | "connect" | "logs";
const TABS: { id: Tab; label: string }[] = [
  { id: "dashboard", label: "Dashboard" },
  { id: "config", label: "Configuration" },
  { id: "connections", label: "Connections & Policy" },
  { id: "simulation", label: "Simulation" },
  { id: "connect", label: "Connect a Client" },
  { id: "logs", label: "Logs" },
];

export function App() {
  const [tab, setTab] = useState<Tab>("dashboard");
  const [volumes, setVolumes] = useState<Record<string, string>>({});
  const [config, setConfig] = useState<GatewayConfig | null>(null);
  const [plugins, setPlugins] = useState<Record<string, string[]>>({});
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [peers, setPeers] = useState<Peer[]>([]);
  const [events, setEvents] = useState<MessageEvent[]>([]);
  const wsRef = useRef<EventStream | null>(null);

  const openWs = useCallback(() => {
    wsRef.current?.close();
    const ws = openEventStream((e) => {
      if (e.direction === "sent" || e.direction === "received") {
        setEvents((prev) => [e, ...prev].slice(0, 200));
      }
    });
    wsRef.current = ws;
  }, []);

  const refreshLive = useCallback(() => {
    api.snapshot().then(setSnapshot).catch(() => {});
    api.health().then(setHealth).catch(() => {});
    api.peers().then(setPeers).catch(() => {});
  }, []);

  const reloadConfig = useCallback(() => {
    api.config().then(setConfig).catch(console.error);
    api.plugins().then(setPlugins).catch(console.error);
  }, []);

  useEffect(() => {
    api.volumes().then(setVolumes).catch(console.error);
    reloadConfig();
    refreshLive();
    openWs();
    const timer = setInterval(refreshLive, 2000);
    return () => {
      clearInterval(timer);
      wsRef.current?.close();
    };
  }, [openWs, refreshLive, reloadConfig]);

  // banner classification = highest of our own marking and anything seen on the net
  const bannerLevel = Math.max(
    config?.classification.level ?? 0,
    snapshot?.max_classification_seen ?? 0
  );

  return (
    <div className="app">
      <div className="banner" style={{ background: classColor(bannerLevel) }}>
        {CLASS_LABELS[bannerLevel]}
        {config ? ` // ${config.classification.releasable_to}` : ""}
      </div>

      <header>
        <h1>JDSSArrow</h1>
        <span className="subtitle">
          Joint Dismounted Soldier System · STANAG 4677 / AEP-76 · Web Config &amp; Monitor
        </span>
      </header>

      <nav className="tabs">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={tab === t.id ? "tab active" : "tab"}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </nav>

      {tab === "dashboard" && (
        <>
          <KpiStrip health={health} snapshot={snapshot} config={config} peers={peers} />
          <div className="grid">
            <HealthPanel health={health} snapshot={snapshot} config={config} />
            <PeersPanel peers={peers} />
            <VolumesPanel volumes={volumes} />
            <ActionsPanel />
            <FeedPanel events={events} />
          </div>
        </>
      )}

      {tab === "config" && (
        <div className="grid">
          <ConfigPanel
            config={config}
            plugins={plugins}
            onApplied={() => {
              reloadConfig();
              refreshLive();
              openWs();
              setEvents([]);
            }}
          />
          <IdentityPanel config={config} />
          <CapabilitiesPanel />
        </div>
      )}

      {tab === "connections" && (
        <div className="grid">
          <ConnectionsPanel />
          <CoalitionPanel />
          <MatrixPanel />
        </div>
      )}

      {tab === "simulation" && (
        <div className="grid">
          <SimulationPanel />
        </div>
      )}

      {tab === "connect" && (
        <div className="grid">
          <ConnectPanel />
        </div>
      )}

      {tab === "logs" && (
        <div className="grid">
          <LogsPanel />
        </div>
      )}

      <div className="banner" style={{ background: classColor(bannerLevel) }}>
        {CLASS_LABELS[bannerLevel]}
      </div>
    </div>
  );
}

function KpiStrip({
  health,
  snapshot,
  config,
  peers,
}: {
  health: Health | null;
  snapshot: Snapshot | null;
  config: GatewayConfig | null;
  peers: Peer[];
}) {
  const connected = peers.filter((p) => p.connected).length;
  const msgs = snapshot
    ? Object.values(snapshot.counts_by_type).reduce((a, b) => a + b, 0)
    : 0;
  const rejected = (health?.dropped_rejected ?? 0) + (health?.dropped_by_policy ?? 0);
  const cards: { label: string; value: string; sub?: string; accent?: string }[] = [
    { label: "Status", value: health?.status?.toUpperCase() ?? "…", accent: "var(--accent)" },
    { label: "Uptime", value: health ? `${Math.round(health.uptime_s)}s` : "…" },
    { label: "Peers", value: String(connected), sub: `${peers.length} known` },
    { label: "Messages", value: String(msgs), sub: `${snapshot?.buffered ?? 0} buffered` },
    { label: "Rejected", value: String(rejected), sub: "auth/policy", accent: rejected ? "#c0392b" : undefined },
    {
      label: "Coalition",
      value: health?.coalition_version ? `v${health.coalition_version}` : "—",
      sub: health?.is_policy_authority ? "authority" : "member",
    },
    {
      label: "Classification",
      value: config ? CLASS_SHORT[config.classification.level] : "…",
      accent: config ? classColor(config.classification.level) : undefined,
    },
  ];
  return (
    <div className="kpis">
      {cards.map((c) => (
        <div className="kpi" key={c.label}>
          <div className="kpi-value" style={{ color: c.accent ?? "var(--text)" }}>
            {c.value}
          </div>
          <div className="kpi-label">{c.label}</div>
          {c.sub && <div className="kpi-sub">{c.sub}</div>}
        </div>
      ))}
    </div>
  );
}

function CapabilitiesPanel() {
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [saved, setSaved] = useState<string>("");
  const load = () => api.capabilities().then(setCaps).catch(console.error);
  useEffect(() => {
    load();
  }, []);
  if (!caps) return <section className="card">Loading capabilities…</section>;

  const toggle = async (t: string, dir: "receive" | "emit", cur: boolean) => {
    const res = await api.setCapability(t, dir, !cur);
    const body = await res.json();
    setSaved(body.persisted ? "✓ saved to config file" : "applied live (no config file to persist)");
    load();
  };

  return (
    <section className="card caps">
      <h2>Capability Matrix (permissions)</h2>
      <p className="hint">
        On/off per JDSS message type. <b>RX</b> = accept inbound (else dropped on ingest);{" "}
        <b>TX</b> = allowed to originate. Applied live &amp; persisted to the config file.{" "}
        {saved && <span style={{ color: "var(--accent)" }}>{saved}</span>}
      </p>
      <table className="capgrid">
        <thead>
          <tr>
            <th>Message type</th>
            <th>RX (receive)</th>
            <th>TX (emit)</th>
          </tr>
        </thead>
        <tbody>
          {caps.types.map((t) => (
            <tr key={t}>
              <td>{t}</td>
              <td>
                <input
                  type="checkbox"
                  checked={caps.receive[t]}
                  onChange={() => toggle(t, "receive", caps.receive[t])}
                />
              </td>
              <td>
                <input
                  type="checkbox"
                  checked={caps.emit[t]}
                  onChange={() => toggle(t, "emit", caps.emit[t])}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function IdentityPanel({ config }: { config: GatewayConfig | null }) {
  if (!config) return <section className="card">…</section>;
  const rows: [string, string][] = [
    ["Node id", config.identity.node_id],
    ["Callsign", config.identity.callsign],
    ["Unit", config.identity.unit],
    ["Nation", config.identity.nation],
    ["Role", config.identity.role],
    ["Network", config.network.network_id],
    ["Multicast", `${config.network.multicast_group ?? "(derived)"}:${config.network.multicast_port ?? ""}`],
  ];
  return (
    <section className="card">
      <h2>Node Identity &amp; Network</h2>
      <table className="kv">
        <tbody>
          {rows.map(([k, v]) => (
            <tr key={k}>
              <td>{k}</td>
              <td>{v}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function VolumesPanel({ volumes }: { volumes: Record<string, string> }) {
  return (
    <section className="card">
      <h2>AEP-76 Volumes</h2>
      <ul className="volumes">
        {Object.entries(volumes).map(([vol, subject]) => (
          <li key={vol}>
            <span className="badge">Vol {vol}</span> {subject}
            <code>jdssarrow.{VOLUME_MODULES[vol]}</code>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ConfigPanel({
  config,
  plugins,
  onApplied,
}: {
  config: GatewayConfig | null;
  plugins: Record<string, string[]>;
  onApplied: () => void;
}) {
  const [draft, setDraft] = useState<GatewayConfig | null>(null);
  const [status, setStatus] = useState<string>("");
  useEffect(() => setDraft(config), [config]);
  if (!draft) return <section className="card">Loading config…</section>;

  const setPlugin = (k: keyof GatewayConfig["plugins"], v: string) =>
    setDraft({ ...draft, plugins: { ...draft.plugins, [k]: v } });

  const rows: [string, keyof GatewayConfig["plugins"], string][] = [
    ["Codec (Vol II)", "codec", "codecs"],
    ["Transport (Vol IV)", "transport", "transports"],
    ["Security (Vol I)", "security", "security"],
    ["Bearer (Vol III)", "bearer", "bearers"],
    ["Allocator (Vol V)", "allocator", "allocators"],
  ];

  const apply = async () => {
    setStatus("applying…");
    try {
      await api.updateConfig({
        plugins: draft.plugins,
        network: { repeat: draft.network.repeat },
        classification: draft.classification,
      });
      setStatus("applied ✓ gateway restarted");
      onApplied();
    } catch (e) {
      setStatus(String(e));
    }
  };

  return (
    <section className="card">
      <h2>Pluggable Configuration</h2>
      <table>
        <tbody>
          {rows.map(([label, key, group]) => (
            <tr key={key}>
              <td>{label}</td>
              <td>
                <select value={draft.plugins[key]} onChange={(e) => setPlugin(key, e.target.value)}>
                  {(plugins[group] ?? [draft.plugins[key]]).map((name) => (
                    <option key={name} value={name}>
                      {name}
                    </option>
                  ))}
                </select>
              </td>
            </tr>
          ))}
          <tr>
            <td>Repeat (reliability)</td>
            <td>
              <input
                type="number"
                min={1}
                max={8}
                value={draft.network.repeat}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    network: { ...draft.network, repeat: Number(e.target.value) },
                  })
                }
              />
            </td>
          </tr>
          <tr>
            <td>Classification</td>
            <td>
              <select
                value={draft.classification.level}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    classification: { ...draft.classification, level: Number(e.target.value) },
                  })
                }
              >
                {[0, 1, 2, 3].map((l) => (
                  <option key={l} value={l}>
                    {CLASS_LABELS[l]}
                  </option>
                ))}
              </select>
            </td>
          </tr>
          <tr>
            <td>Releasable to</td>
            <td>
              <input
                value={draft.classification.releasable_to}
                onChange={(e) =>
                  setDraft({
                    ...draft,
                    classification: { ...draft.classification, releasable_to: e.target.value },
                  })
                }
              />
            </td>
          </tr>
        </tbody>
      </table>
      <button onClick={apply}>Apply &amp; restart gateway</button>
      <span className="status">{status}</span>
      <p className="hint">
        Every seam is a runtime plugin (values from <code>/api/plugins</code>). Applying hot-restarts
        the gateway on the new selection.
      </p>
    </section>
  );
}

function HealthPanel({
  health,
  snapshot,
  config,
}: {
  health: Health | null;
  snapshot: Snapshot | null;
  config: GatewayConfig | null;
}) {
  return (
    <section className="card">
      <h2>System Health &amp; Monitor</h2>
      {!health ? (
        "…"
      ) : (
        <>
          <p>
            <span className={`dot ${health.engine_running ? "up" : "down"}`} />
            <strong>{health.status.toUpperCase()}</strong> · {config?.identity.callsign} (
            {health.node_id}) · net <strong>{config?.network.network_id}</strong>
          </p>
          <table className="kv">
            <tbody>
              <tr><td>Uptime</td><td>{health.uptime_s}s</td></tr>
              <tr><td>Engine</td><td>{health.engine_running ? "running" : "stopped"}</td></tr>
              <tr><td>OS threads</td><td>{health.threads}</td></tr>
              <tr><td>asyncio tasks</td><td>{health.asyncio_tasks}</td></tr>
              <tr><td>WS subscribers</td><td>{health.ws_subscribers}</td></tr>
              <tr><td>Telemetry buffered</td><td>{health.telemetry_buffered}</td></tr>
              <tr>
                <td>Active stack</td>
                <td>
                  {health.codec}/{health.transport}/{health.security} ×{health.repeat}
                </td>
              </tr>
            </tbody>
          </table>
          <div className="counts">
            {snapshot &&
              Object.entries(snapshot.counts_by_type).map(([t, c]) => (
                <span key={t} className="chip" style={{ borderColor: typeColor(t) }}>
                  {t} <strong>{c}</strong>
                </span>
              ))}
          </div>
        </>
      )}
    </section>
  );
}

function PeersPanel({ peers }: { peers: Peer[] }) {
  const [selected, setSelected] = useState<Peer | null>(null);
  return (
    <section className="card">
      <h2>Connected Peers ({peers.filter((p) => p.connected).length})</h2>
      {peers.length === 0 ? (
        <p className="hint">No peers seen yet.</p>
      ) : (
        <table className="peers">
          <thead>
            <tr>
              <th></th>
              <th>Callsign</th>
              <th>Nation</th>
              <th>Role</th>
              <th>Class</th>
              <th>Msgs</th>
              <th>Seen</th>
            </tr>
          </thead>
          <tbody>
            {peers.map((p) => (
              <tr
                key={p.node_id}
                className={`clickable ${p.connected ? "" : "stale"}`}
                title="click for peer details"
                onClick={() => setSelected(p)}
              >
                <td>
                  <span className={`dot ${p.connected ? "up" : "down"}`} />
                </td>
                <td>{p.callsign ?? p.node_id}</td>
                <td>{p.nation ?? "—"}</td>
                <td>{p.role ?? "—"}</td>
                <td>
                  <ClassBadge level={p.classification} />
                </td>
                <td>{p.messages}</td>
                <td>{p.seconds_ago}s</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {selected && <PeerModal peer={selected} onClose={() => setSelected(null)} />}
    </section>
  );
}

function ActionsPanel() {
  const [text, setText] = useState("SITREP: all quiet");
  return (
    <section className="card">
      <h2>Inject (Web → Network)</h2>
      <div className="row">
        <input value={text} onChange={(e) => setText(e.target.value)} />
        <button onClick={() => api.publishChat(text)}>Send chat</button>
      </div>
      <button onClick={() => api.publishContact(50.851, 4.352, "Web-reported contact")}>
        Report contact
      </button>
    </section>
  );
}

function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <div className="code">
      <button className="copy" onClick={copy}>
        {copied ? "copied ✓" : "copy"}
      </button>
      <pre>{code}</pre>
    </div>
  );
}

function ConnectPanel() {
  const [info, setInfo] = useState<ConnectInfo | null>(null);
  const [reveal, setReveal] = useState(false);
  useEffect(() => {
    api.connect().then(setInfo).catch(console.error);
  }, []);
  if (!info) return <section className="card">Loading connection info…</section>;

  // the coalition PSK is a shared secret — masked until explicitly revealed
  const psk = reveal ? info.psk : "‹reveal PSK above›";

  const yamlCfg = `# node-x.yaml — a new client joining this JDSS network
identity:
  node_id: node-x
  callsign: XRAY-9
  nation: XXX
  role: rifleman
plugins:
  codec: ${info.codec}
  transport: udp
  security: ${info.security}
network:
  network_id: ${info.network_id}
  psk: ${psk}          # coalition shared key — must match
  repeat: ${info.repeat}${
    info.policy_authority
      ? `
connections:
  policy_authority: ${info.policy_authority}
  authority_public_key: ${info.authority_public_key ?? "<authority Ed25519 pubkey>"}`
      : ""
  }`;

  const cli = `# install, then run the node — it joins ${info.multicast_group}:${info.port}
uv pip install -e ".[dev]"
jdssarrow run --config node-x.yaml --casevac`;

  const py = `import asyncio
from jdssarrow.config.models import GatewayConfig, NodeIdentity, PluginSelection, NetworkConfig
from jdssarrow.gateway.gateway import JdssGateway
from jdssarrow.gateway.node import SoldierNode

cfg = GatewayConfig(
    identity=NodeIdentity(node_id="node-x", callsign="XRAY-9"),
    plugins=PluginSelection(codec="${info.codec}", transport="udp", security="${info.security}"),
    network=NetworkConfig(network_id="${info.network_id}", psk="${psk}"),
)

async def main():
    node = SoldierNode(JdssGateway(cfg))
    await node.start()
    await node.identify()
    await node.presence(50.85, 4.35, battery_pct=90)   # appears on every peer's COP
    await node.report_contact(50.86, 4.36, "dismounted patrol")
    await asyncio.sleep(2)
    await node.stop()

asyncio.run(main())`;

  const atak = `ATAK / external EUD (Cursor-on-Target) — via a bridge

ATAK speaks CoT XML over the TAK protocol, not JDSSDM. To bring an ATAK EUD onto this
network, run a small CoT <-> JDSS bridge that joins the multicast group and maps:

  CoT event (a-f-G-U-C…)  ->  Presence / ContactSighting   (with APP-6(D) SIDC)
  CoT chat (GeoChat)      ->  Chat
  CoT markers / drawings  ->  Overlay graphics
  CoT medevac (9-line)    ->  CasevacRequest

Join coordinates for the bridge:
  group:    ${info.multicast_group}:${info.port}
  codec:    ${info.codec}   (${info.codec_content_type})
  security: ${info.security}  (HMAC-SHA256 over the payload, key = coalition PSK)
  network:  ${info.network_id}

(No ATAK bridge is bundled — this is the integration contract to implement one.)`;

  const raw = `Any language — raw wire format (implement the frame + auth):

  frame = "${info.frame.magic}"(4) | version=${info.frame.version}(1)
        | codec-name | security-name | length(4, big-endian) | payload
  payload = ${info.frame.payload}

  1. build a JDSSDM message (header + typed body) and encode with the "${info.codec}" codec
  2. append/verify HMAC-SHA256(payload, key="${psk}")   [security="${info.security}"]
  3. send the frame to UDP multicast ${info.multicast_group}:${info.port} (send it ${info.repeat}x for reliability)
  4. receivers dedup by (originator_id, message_id)

Message types on this network: ${info.message_types.join(", ")}`;

  return (
    <section className="card connect">
      <h2>Connect a Client to this JDSS Network</h2>
      <table className="kv joinparams">
        <tbody>
          <tr><td>Network</td><td>{info.network_id}</td></tr>
          <tr><td>Multicast</td><td>{info.multicast_group}:{info.port}</td></tr>
          <tr><td>Codec (Vol II)</td><td>{info.codec} <span className="hint">({info.codec_content_type})</span></td></tr>
          <tr><td>Security (Vol I)</td><td>{info.security} — HMAC-SHA256, key = coalition PSK</td></tr>
          <tr>
            <td>Coalition PSK</td>
            <td>
              <code>{reveal ? info.psk : "•".repeat(Math.min(12, info.psk.length))}</code>{" "}
              <button className="reveal-btn" onClick={() => setReveal((r) => !r)}>
                {reveal ? "hide" : "reveal"}
              </button>
              {!reveal && <span className="hint"> shared secret</span>}
            </td>
          </tr>
          <tr><td>Policy authority</td><td>{info.policy_authority ?? "—"}</td></tr>
        </tbody>
      </table>
      <p className="hint">
        The coalition PSK is a shared secret — masked by default. <b>Reveal</b> it to copy
        working snippets (they show <code>‹reveal PSK above›</code> until then).
      </p>

      <h3>① National soldier system / CLI node</h3>
      <p className="hint">Drop this config and run the bundled node — joins over real UDP multicast.</p>
      <CodeBlock code={yamlCfg} />
      <CodeBlock code={cli} />

      <h3>② Custom client — Python SDK</h3>
      <CodeBlock code={py} />

      <h3>③ ATAK / external EUD (Cursor-on-Target)</h3>
      <CodeBlock code={atak} />

      <h3>④ Any language — raw wire</h3>
      <CodeBlock code={raw} />
    </section>
  );
}

function LogsPanel() {
  const [msgs, setMsgs] = useState<MsgLogEntry[]>([]);
  const [counts, setCounts] = useState<Record<string, number>>({});
  const [appLog, setAppLog] = useState<AppLogRecord[]>([]);
  const [dir, setDir] = useState("");
  const [disp, setDisp] = useState("");

  useEffect(() => {
    const load = () => {
      api
        .messageLog(dir, disp)
        .then((r) => {
          setMsgs([...r.entries].reverse());
          setCounts(r.counts);
        })
        .catch(console.error);
      api.appLog().then((r) => setAppLog([...r].reverse())).catch(console.error);
    };
    load();
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, [dir, disp]);

  const hhmmss = (ts: number) => new Date(ts * 1000).toISOString().slice(11, 19);

  return (
    <>
      <section className="card">
        <h2>Application Log</h2>
        <p className="hint">Lifecycle, warnings and errors from the node (newest first).</p>
        <ul className="applog">
          {appLog.length === 0 && <li className="hint">no records</li>}
          {appLog.map((r, i) => (
            <li key={i} className={`lvl-${r.level.toLowerCase()}`}>
              <span className="time">{hhmmss(r.ts)}</span>
              <span className="level">{r.level}</span>
              <span className="lname">{r.logger}</span>
              <span className="lmsg">{r.message}</span>
            </li>
          ))}
        </ul>
      </section>

      <section className="card msglog">
        <h2>Message Log — incoming / outgoing, accepted / rejected</h2>
        <div className="logfilters">
          <label>
            direction{" "}
            <select value={dir} onChange={(e) => setDir(e.target.value)}>
              <option value="">all</option>
              <option value="in">incoming</option>
              <option value="out">outgoing</option>
            </select>
          </label>
          <label>
            disposition{" "}
            <select value={disp} onChange={(e) => setDisp(e.target.value)}>
              <option value="">all</option>
              <option value="accepted">accepted</option>
              <option value="rejected">rejected</option>
            </select>
          </label>
          <span className="hint">
            in {counts.in ?? 0} · out {counts.out ?? 0} · accepted {counts.accepted ?? 0} ·{" "}
            <span style={{ color: "#c0392b" }}>rejected {counts.rejected ?? 0}</span>
          </span>
        </div>
        <div className="loghead">
          <span></span>
          <span>Type</span>
          <span>From</span>
          <span>Disposition / reason</span>
          <span>Time</span>
        </div>
        <ul>
          {msgs.map((e, i) => (
            <li key={i} className={e.disposition}>
              <span className="dir">{e.direction === "out" ? "▲ TX" : "▼ RX"}</span>
              <span className="type" style={{ color: typeColor(e.type ?? "") }}>
                {e.type ?? "—"}
              </span>
              <span className="from">{e.originator_id ?? "—"}</span>
              <span className="disp">
                {e.disposition === "accepted" ? (
                  <span className="ok">accepted</span>
                ) : (
                  <span className="rej">✗ {e.reason}</span>
                )}
              </span>
              <span className="time">{hhmmss(e.ts)}</span>
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}

function SimulationPanel() {
  const [status, setStatus] = useState<SimStatus | null>(null);
  const [interval, setIntervalS] = useState(1);
  const [rogue, setRogue] = useState(false);
  const [isolated, setIsolated] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = () => api.simStatus().then(setStatus).catch(console.error);
  useEffect(() => {
    load();
    const t = setInterval(load, 1500);
    return () => clearInterval(t);
  }, []);

  const start = async () => {
    setBusy(true);
    await api.simStart({ interval, rogue: rogue ? "garbage" : null, isolated });
    await load();
    setBusy(false);
  };
  const stop = async () => {
    setBusy(true);
    await api.simStop();
    await load();
    setBusy(false);
  };

  const running = status?.running ?? false;
  return (
    <section className="card sim">
      <h2>Simulation Control</h2>
      <p className="hint">
        Spawns a roster of JDSS-compliant clients. By default they join <b>this node's own
        network</b>, so the Dashboard, Peers, Matrix and Coalition views come alive.
      </p>

      <div className="sim-row">
        <span className={`dot ${running ? "up" : "down"}`} />
        <b>{running ? "RUNNING" : "stopped"}</b>
        {running && status && (
          <span className="hint">
            {" "}
            · {status.client_count} clients · {status.ticks} ticks · net{" "}
            <b>{status.network_id}</b> · {status.transport}
            {status.rogue ? ` · rogue: ${status.rogue}` : ""}
          </span>
        )}
      </div>

      <div className="sim-controls">
        <label>
          tick interval (s){" "}
          <input
            type="number"
            min={0.1}
            step={0.1}
            value={interval}
            disabled={running}
            onChange={(e) => setIntervalS(Number(e.target.value))}
          />
        </label>
        <label>
          <input
            type="checkbox"
            checked={rogue}
            disabled={running}
            onChange={(e) => setRogue(e.target.checked)}
          />{" "}
          include rogue client
        </label>
        <label>
          <input
            type="checkbox"
            checked={isolated}
            disabled={running}
            onChange={(e) => setIsolated(e.target.checked)}
          />{" "}
          isolated (loopback, separate net)
        </label>
      </div>

      {running ? (
        <button className="danger" disabled={busy} onClick={stop}>
          Stop simulation
        </button>
      ) : (
        <button disabled={busy} onClick={start}>
          Start simulation
        </button>
      )}

      {status && status.clients.length > 0 && (
        <table className="conns" style={{ marginTop: "1rem" }}>
          <tbody>
            {status.clients.map((c) => (
              <tr key={c.node_id}>
                <td>{c.callsign}</td>
                <td>
                  <span className="devtag">{c.device ?? "—"}</span>
                </td>
                <td>{c.role}</td>
                <td className="hint">{c.node_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

function ConnectionsPanel() {
  const [conn, setConn] = useState<Connections | null>(null);
  const load = () => api.connections().then(setConn).catch(console.error);
  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);
  if (!conn) return <section className="card">Loading connections…</section>;

  const state = (peer: string) =>
    conn.policy.overrides[peer] ?? conn.policy.default_action;
  const act = async (peer: string, action: "allow" | "block" | "reset") => {
    await api.setConnection(peer, action);
    load();
  };

  return (
    <section className="card">
      <h2>Connection Management</h2>
      <p className="hint">
        default: <b>{conn.policy.default_action}</b> · dropped by policy:{" "}
        <b>{conn.dropped_by_policy}</b>
      </p>
      {conn.peers.length === 0 ? (
        <p className="hint">No peers to manage yet.</p>
      ) : (
        <table className="conns">
          <tbody>
            {conn.peers.map((p) => {
              const blocked = state(p.node_id) === "block";
              return (
                <tr key={p.node_id} className={blocked ? "blocked" : ""}>
                  <td>
                    <span className={`dot ${blocked ? "down" : "up"}`} />
                    {p.callsign ?? p.node_id}
                  </td>
                  <td>{blocked ? "BLOCKED" : "accepted"}</td>
                  <td>
                    {blocked ? (
                      <button onClick={() => act(p.node_id, "allow")}>Allow</button>
                    ) : (
                      <button className="danger" onClick={() => act(p.node_id, "block")}>
                        Block
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </section>
  );
}

function CoalitionPanel() {
  const [pol, setPol] = useState<CoalitionPolicy | null>(null);
  const [peers, setPeers] = useState<Peer[]>([]);
  const load = () => {
    api.coalition().then(setPol).catch(console.error);
    api.peers().then(setPeers).catch(() => {});
  };
  useEffect(() => {
    load();
    const t = setInterval(load, 3000);
    return () => clearInterval(t);
  }, []);
  if (!pol) return <section className="card">Loading coalition policy…</section>;

  const blockedList = Object.entries(pol.overrides)
    .filter(([, v]) => v === "block")
    .map(([p]) => p);
  const act = async (peer: string, action: "allow" | "block" | "reset") => {
    await api.setCoalition(peer, action);
    load();
  };

  return (
    <section className="card">
      <h2>Coalition Policy (gossip-distributed)</h2>
      {!pol.enabled ? (
        <p className="hint">No policy authority configured for this network.</p>
      ) : (
        <>
          <p className="hint">
            authority: <b>{pol.authority_id}</b> · v{pol.version} ·{" "}
            {pol.am_authority ? (
              <span style={{ color: "var(--accent)" }}>you are the AUTHORITY</span>
            ) : (
              <span>read-only (distributed to this node)</span>
            )}{" "}
            · default <b>{pol.default_action}</b> ·{" "}
            {pol.signed ? (
              <span style={{ color: "var(--accent)" }}>🔏 Ed25519-signed</span>
            ) : (
              <span style={{ color: "#d29a2b" }}>unsigned</span>
            )}
          </p>
          <p>
            Network-wide blocked:{" "}
            {blockedList.length ? (
              blockedList.map((p) => (
                <span key={p} className="cbadge" style={{ background: "#c0392b", marginRight: 4 }}>
                  {p}
                </span>
              ))
            ) : (
              <span className="hint">none</span>
            )}
          </p>
          {pol.am_authority && (
            <table className="conns">
              <tbody>
                {peers.map((p) => {
                  const blocked = pol.overrides[p.node_id] === "block";
                  return (
                    <tr key={p.node_id} className={blocked ? "blocked" : ""}>
                      <td>{p.callsign ?? p.node_id}</td>
                      <td>{blocked ? "coalition-BLOCKED" : "allowed"}</td>
                      <td>
                        {blocked ? (
                          <button onClick={() => act(p.node_id, "reset")}>Unblock</button>
                        ) : (
                          <button className="danger" onClick={() => act(p.node_id, "block")}>
                            Block network-wide
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </>
      )}
    </section>
  );
}

function MatrixPanel() {
  const [matrix, setMatrix] = useState<ConnMatrix | null>(null);
  const [mode, setMode] = useState<"live" | "probe">("live");
  const [cell, setCell] = useState<{ obs: string; from: string } | null>(null);
  const load = (m: "live" | "probe") =>
    (m === "live" ? api.matrix() : api.matrixProbe("garbage"))
      .then(setMatrix)
      .catch(console.error);
  useEffect(() => {
    load(mode);
    if (mode === "live") {
      const t = setInterval(() => load("live"), 2500);
      return () => clearInterval(t);
    }
  }, [mode]);

  const short = (n: string) => n.replace(/-1$/, "").slice(0, 8);
  // coalition block applies to EVERY row (network-wide); local block only to the local row
  const coalitionBlocks = (from: string): boolean => {
    const c = matrix?.coalition;
    if (!c || !c.enabled) return false;
    return (c.overrides[from] ?? c.default_action) === "block";
  };
  const localBlocks = (obs: string, from: string): boolean => {
    const pol = matrix?.policy;
    if (!pol || obs !== pol.node_id) return false;
    return (pol.overrides[from] ?? pol.default_action) === "block";
  };
  const isBlocked = (obs: string, from: string): boolean =>
    coalitionBlocks(from) || localBlocks(obs, from);
  const cellStyle = (obs: string, from: string, v: number): CSSProperties => {
    if (obs === from) return { color: "var(--muted)" };
    if (isBlocked(obs, from)) return { color: "#c0392b", fontWeight: 700 };
    if (from === matrix?.rogue_node) return { color: "#c0392b", opacity: v ? 1 : 0.5 };
    return { color: v ? "var(--text)" : "var(--muted)", opacity: v ? 1 : 0.35 };
  };

  return (
    <section className="card matrix">
      <h2>Connection Matrix — who accepts whom</h2>
      <label className="rogue-toggle">
        <input
          type="radio"
          checked={mode === "live"}
          onChange={() => setMode("live")}
        />
        live (peer-digest gossip)
      </label>{" "}
      <label className="rogue-toggle">
        <input
          type="radio"
          checked={mode === "probe"}
          onChange={() => setMode("probe")}
        />
        probe + rogue (simulated)
      </label>{" "}
      <button onClick={() => load(mode)}>Refresh</button>
      {!matrix ? (
        <p className="hint">probing…</p>
      ) : (
        <table className="matrixgrid">
          <thead>
            <tr>
              <th>obs ← from</th>
              {matrix.nodes.map((n) => (
                <th key={n} className={n === matrix.rogue_node ? "rogue" : ""}>
                  {short(n)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {matrix.nodes.map((obs) => (
              <tr key={obs}>
                <td className={`rowhead ${obs === matrix.rogue_node ? "rogue" : ""}`}>
                  {short(obs)}
                </td>
                {matrix.nodes.map((from) => {
                  const v = matrix.rows[obs]?.[from] ?? 0;
                  const self = obs === from;
                  return (
                    <td
                      key={from}
                      style={cellStyle(obs, from, v)}
                      className={self ? "" : "cell-click"}
                      title={self ? "" : `${obs} ← ${from}: click for details`}
                      onClick={self ? undefined : () => setCell({ obs, from })}
                    >
                      {self ? "–" : isBlocked(obs, from) ? "✗" : v}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="hint">
        Cell = messages the row node accepted from the column node. <b>Live</b> assembles the
        matrix from peer-digest gossip across the real coalition network (each node broadcasts
        its own row). <b>Probe</b> runs a short in-process exercise with a rogue, whose column
        stays all-zero because nobody accepts it.
      </p>
      {matrix && cell && (
        <MatrixCellModal matrix={matrix} cell={cell} onClose={() => setCell(null)} />
      )}
    </section>
  );
}

function FeedPanel({ events }: { events: MessageEvent[] }) {
  const [selected, setSelected] = useState<MessageEvent | null>(null);
  return (
    <section className="card feed">
      <h2>Live Message Feed</h2>
      <div className="feedhead">
        <span></span>
        <span>Type</span>
        <span>From</span>
        <span>Class</span>
        <span>Time</span>
      </div>
      <ul>
        {events.map((e) => (
          <li
            key={e.message_id}
            className={`${e.direction} clickable`}
            style={{ borderLeftColor: typeColor(e.type) }}
            title="click to inspect the full message"
            onClick={() => setSelected(e)}
          >
            <span className="dir">{e.direction === "sent" ? "▲ TX" : "▼ RX"}</span>
            <span className="type" style={{ color: typeColor(e.type) }}>
              {e.type}
            </span>
            <span className="from">
              {e.callsign ? `${e.callsign} (${e.originator_id})` : e.originator_id}
            </span>
            <span className="cls">
              <ClassBadge level={e.classification ?? 0} />
            </span>
            <span className="time">{e.reporting_time?.slice(11, 19)}</span>
          </li>
        ))}
      </ul>
      {selected && <MessageModal event={selected} onClose={() => setSelected(null)} />}
    </section>
  );
}

function StructuredValue({ value }: { value: unknown }) {
  if (value === null || value === undefined || value === "")
    return <span className="jnil">—</span>;
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="jnil">[ ]</span>;
    return (
      <ol className="jarr">
        {value.map((v, i) => (
          <li key={i}>
            <StructuredValue value={v} />
          </li>
        ))}
      </ol>
    );
  }
  if (typeof value === "object") {
    return (
      <table className="jobj">
        <tbody>
          {Object.entries(value as Record<string, unknown>).map(([k, v]) => (
            <tr key={k}>
              <td className="jk">{k}</td>
              <td>
                <StructuredValue value={v} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  return <span className="jv">{String(value)}</span>;
}

function Modal({
  title,
  onClose,
  children,
}: {
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button className="modal-x" onClick={onClose}>
            ✕
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function KvTable({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <table className="kv">
      <tbody>
        {rows.map(([k, v]) => (
          <tr key={k}>
            <td>{k}</td>
            <td>{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function CopyJson({ value }: { value: unknown }) {
  return (
    <div className="modal-actions">
      <button onClick={() => navigator.clipboard?.writeText(JSON.stringify(value, null, 2))}>
        copy JSON
      </button>
      <span className="hint">Esc or click outside to close</span>
    </div>
  );
}

function MessageModal({ event, onClose }: { event: MessageEvent; onClose: () => void }) {
  const full = {
    header: {
      type: event.type,
      message_id: event.message_id,
      originator_id: event.originator_id,
      callsign: event.callsign,
      reporting_time: event.reporting_time,
      classification: event.classification,
      releasable_to: event.releasable_to,
    },
    body: event.body,
  };
  const headerRows: [string, ReactNode][] = [
    ["Type", <span style={{ color: typeColor(event.type) }}>{event.type}</span>],
    ["Direction", event.direction === "sent" ? "▲ transmitted" : "▼ received"],
    ["Originator", event.callsign ? `${event.callsign} (${event.originator_id})` : event.originator_id],
    ["Message id", <code>{event.message_id}</code>],
    ["Reporting time", event.reporting_time ?? "—"],
    [
      "Classification",
      <span>
        <ClassBadge level={event.classification ?? 0} />{" "}
        {CLASS_LABELS[event.classification ?? 0]} · {event.releasable_to ?? "—"}
      </span>,
    ],
  ];
  return (
    <Modal
      title={
        <>
          <span style={{ color: typeColor(event.type) }}>{event.type}</span> message
        </>
      }
      onClose={onClose}
    >
      <h3>Header</h3>
      <KvTable rows={headerRows} />
      <h3>Body (JDSSDM)</h3>
      <StructuredValue value={event.body ?? {}} />
      <CopyJson value={full} />
    </Modal>
  );
}

function PeerModal({ peer, onClose }: { peer: Peer; onClose: () => void }) {
  const rows: [string, ReactNode][] = [
    ["Callsign", peer.callsign ?? "—"],
    ["Node id", <code>{peer.node_id}</code>],
    ["Nation", peer.nation ?? "—"],
    ["Unit", peer.unit ?? "—"],
    ["Role", peer.role ?? "—"],
    [
      "Classification",
      <span>
        <ClassBadge level={peer.classification} /> {CLASS_LABELS[peer.classification]} ·{" "}
        {peer.releasable_to}
      </span>,
    ],
    ["Messages", String(peer.messages)],
    ["Last seen", `${peer.seconds_ago}s ago`],
    [
      "Status",
      <span>
        <span className={`dot ${peer.connected ? "up" : "down"}`} />
        {peer.connected ? "connected" : "stale"}
      </span>,
    ],
  ];
  return (
    <Modal title={<>Peer · {peer.callsign ?? peer.node_id}</>} onClose={onClose}>
      <h3>Identity &amp; link</h3>
      <KvTable rows={rows} />
      <CopyJson value={peer} />
    </Modal>
  );
}

function MatrixCellModal({
  matrix,
  cell,
  onClose,
}: {
  matrix: ConnMatrix;
  cell: { obs: string; from: string };
  onClose: () => void;
}) {
  const { obs, from } = cell;
  const count = matrix.rows[obs]?.[from] ?? 0;
  const coalitionBlock =
    matrix.coalition && matrix.coalition.enabled
      ? (matrix.coalition.overrides[from] ?? matrix.coalition.default_action) === "block"
      : false;
  const localBlock =
    matrix.policy && obs === matrix.policy.node_id
      ? (matrix.policy.overrides[from] ?? matrix.policy.default_action) === "block"
      : false;

  let status: ReactNode;
  if (coalitionBlock)
    status = <span style={{ color: "#c0392b" }}>✗ blocked network-wide (coalition policy)</span>;
  else if (localBlock)
    status = <span style={{ color: "#c0392b" }}>✗ blocked locally by {obs}</span>;
  else if (count > 0) status = <span style={{ color: "var(--accent)" }}>accepting traffic</span>;
  else status = <span className="jnil">no traffic accepted</span>;

  const rows: [string, ReactNode][] = [
    ["Observer (accepts)", <code>{obs}</code>],
    ["Originator (from)", <code>{from}</code>],
    ["Messages accepted", String(count)],
    ["Status", status],
  ];
  return (
    <Modal title={<>Connection · {obs} ← {from}</>} onClose={onClose}>
      <p className="hint">
        How many messages the row node ({obs}) has accepted from the column node ({from}).
      </p>
      <KvTable rows={rows} />
    </Modal>
  );
}
