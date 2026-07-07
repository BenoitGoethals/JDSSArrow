// Typed client for the JDSSArrow backend API.

export interface GatewayConfig {
  identity: { node_id: string; callsign: string; unit: string; nation: string; role: string };
  plugins: {
    codec: string;
    transport: string;
    security: string;
    bearer: string;
    allocator: string;
  };
  network: {
    network_id: string;
    multicast_group: string | null;
    multicast_port: number | null;
    repeat: number;
    psk: string;
  };
  classification: { level: number; releasable_to: string };
  web: { host: string; port: number; telemetry_capacity: number; peer_timeout_s: number };
}

export interface Snapshot {
  node_id: string;
  known_nodes: string[];
  counts_by_type: Record<string, number>;
  buffered: number;
  max_classification_seen: number;
}

export interface Health {
  status: string;
  node_id: string;
  uptime_s: number;
  engine_running: boolean;
  transport: string;
  codec: string;
  security: string;
  repeat: number;
  threads: number;
  asyncio_tasks: number;
  telemetry_buffered: number;
  ws_subscribers: number;
  classification: number;
  releasable_to: string;
  dropped_rejected: number;
  dropped_by_policy: number;
  policy_authority: string | null;
  is_policy_authority: boolean;
  coalition_version: number;
  gossip_remote_rows: number;
}

export interface Peer {
  node_id: string;
  callsign: string | null;
  unit: string | null;
  nation: string | null;
  role: string | null;
  messages: number;
  classification: number;
  releasable_to: string;
  seconds_ago: number;
  connected: boolean;
}

export interface Policy {
  node_id: string;
  default_action: string;
  overrides: Record<string, string>; // peer -> "allow" | "block"
}

export interface CoalitionPolicy {
  default_action: string;
  overrides: Record<string, string>;
  authority_id: string | null;
  am_authority: boolean;
  version: number;
  enabled: boolean;
  signed: boolean;
  pairs?: Record<string, string[]>; // observer -> blocked originators (the communication matrix)
}

export interface ConnMatrix {
  nodes: string[];
  rows: Record<string, Record<string, number>>;
  rogue_node: string | null;
  policy?: Policy;
  coalition?: CoalitionPolicy;
}

export interface Connections {
  policy: Policy;
  peers: Peer[];
  dropped_by_policy: number;
}

export interface SimStatus {
  running: boolean;
  ticks: number;
  client_count: number;
  clients: { node_id: string; callsign: string; role: string; device?: string }[];
  network_id?: string;
  transport?: string;
  interval?: number;
  rogue?: string | null;
}

export interface Capabilities {
  types: string[];
  receive: Record<string, boolean>;
  emit: Record<string, boolean>;
}

export interface MsgLogEntry {
  ts: number;
  direction: "in" | "out";
  disposition: "accepted" | "rejected";
  reason: string | null;
  type: string | null;
  originator_id: string | null;
  message_id: string | null;
  callsign: string | null;
  classification: number | null;
  releasable_to: string | null;
  network_id: string | null;
  sequence: number | null;
  reporting_time: string | null;
  body: Record<string, unknown> | null;
}

export interface AppLogRecord {
  ts: number;
  level: string;
  logger: string;
  message: string;
}

export interface ConnectInfo {
  network_id: string;
  multicast_group: string;
  port: number;
  codec: string;
  codec_content_type: string;
  security: string;
  psk: string;
  repeat: number;
  classification: number;
  releasable_to: string;
  message_types: string[];
  policy_authority: string | null;
  authority_public_key: string | null;
  frame: { magic: string; version: number; layout: string; payload: string };
}

export interface MessageEvent {
  direction: "sent" | "received" | "snapshot" | "heartbeat";
  type?: string;
  originator_id?: string;
  callsign?: string | null;
  message_id?: string;
  reporting_time?: string;
  classification?: number;
  releasable_to?: string;
  body?: Record<string, unknown>;
  snapshot?: Snapshot;
}

// A CoT/TAK server connection (editable fields sent to the API).
export interface ServerInput {
  name: string;
  host: string;
  port: number;
  tls: boolean;
  verify: boolean;
  client_cert: string | null;
  client_key: string | null;
  cacert: string | null;
  enabled: boolean;
}

// A server connection as returned by the API (input fields + live status).
export interface ServerConnection extends ServerInput {
  id: string;
  state: "connected" | "connecting" | "disabled" | "stopped";
  connected: boolean;
  connects: number;
  reconnects: number;
  queued: number;
  dropped: number;
  last_error: string | null;
}

// One CoT frame crossing a TAK-server / EUD bridge.
export interface CotLogEntry {
  ts: number;
  peer: string;
  direction: "in" | "out";
  type: string | null;
  uid: string | null;
  callsign: string | null;
  remarks: string | null;
  size: number;
  raw: string;
}
export interface CotLog {
  counts: Record<string, number>;
  entries: CotLogEntry[];
}

// Built-in ATAK/EUD TAK server status + config.
export interface EudStatus {
  enabled: boolean;
  listening: boolean;
  host: string;
  port: number;
  advertised_host: string | null;
  clients: string[];
  client_count: number;
  last_error: string | null;
  lan_ip: string;
}

/** Signal the app to fall back to the login screen when a session is missing/expired. */
function onUnauthorized() {
  window.dispatchEvent(new Event("jdss-unauthorized"));
}

async function json<T>(url: string): Promise<T> {
  const res = await fetch(url, { credentials: "same-origin" });
  if (res.status === 401) {
    onUnauthorized();
    throw new Error(`${url}: 401`);
  }
  if (!res.ok) throw new Error(`${url}: ${res.status}`);
  return res.json() as Promise<T>;
}

export interface CurrentUser {
  username: string;
}

export const api = {
  // ---- auth ----
  login: (username: string, password: string) =>
    jsonBody<CurrentUser>("/api/auth/login", "POST", { username, password }),
  logout: () => jsonBody<{ ok: boolean }>("/api/auth/logout", "POST"),
  me: () => json<CurrentUser>("/api/auth/me"),

  volumes: () => json<Record<string, string>>("/api/volumes"),
  config: () => json<GatewayConfig>("/api/config"),
  plugins: () => json<Record<string, string[]>>("/api/plugins"),
  snapshot: () => json<Snapshot>("/api/monitor/snapshot"),
  health: () => json<Health>("/api/health"),
  peers: () => json<Peer[]>("/api/monitor/peers"),
  classifications: () => json<Record<string, string>>("/api/classifications"),
  matrix: () => json<ConnMatrix>("/api/monitor/matrix"),
  matrixProbe: (rogue?: string) =>
    json<ConnMatrix>(`/api/monitor/matrix/probe?ticks=12${rogue ? `&rogue=${rogue}` : ""}`),
  connections: () => json<Connections>("/api/connections"),
  setConnection: (peer: string, action: "allow" | "block" | "reset") =>
    fetch(`/api/connections/${peer}?action=${action}`, { method: "POST" }),
  coalition: () => json<CoalitionPolicy>("/api/coalition"),
  setCoalition: (peer: string, action: "allow" | "block" | "reset") =>
    fetch(`/api/coalition/${peer}?action=${action}`, { method: "POST" }),
  setCoalitionPair: (observer: string, originator: string, action: "block" | "allow" | "reset") =>
    fetch(
      `/api/coalition/pair?observer=${encodeURIComponent(observer)}&originator=${encodeURIComponent(
        originator
      )}&action=${action}`,
      { method: "POST" }
    ),
  simStatus: () => json<SimStatus>("/api/sim"),
  simStart: (body: { interval?: number; rogue?: string | null; isolated?: boolean }) =>
    fetch("/api/sim/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  simStop: () => fetch("/api/sim/stop", { method: "POST" }),
  connect: () => json<ConnectInfo>("/api/connect"),
  messageLog: (direction?: string, disposition?: string) => {
    const q = new URLSearchParams({ limit: "200" });
    if (direction) q.set("direction", direction);
    if (disposition) q.set("disposition", disposition);
    return json<{ counts: Record<string, number>; entries: MsgLogEntry[] }>(
      `/api/logs/messages?${q}`
    );
  },
  appLog: () => json<AppLogRecord[]>("/api/logs/app?limit=200"),
  capabilities: () => json<Capabilities>("/api/capabilities"),
  setCapability: (type: string, direction: "receive" | "emit", allowed: boolean) =>
    fetch(`/api/capabilities/${type}?direction=${direction}&allowed=${allowed}`, {
      method: "POST",
    }),
  updateConfig: async (patch: Record<string, unknown>): Promise<GatewayConfig> => {
    const res = await fetch("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    });
    if (!res.ok) throw new Error(`apply failed: ${res.status} ${await res.text()}`);
    return res.json() as Promise<GatewayConfig>;
  },
  publishContact: (lat: number, lon: number, description: string) =>
    fetch("/api/publish/contact", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat, lon, description }),
    }),
  publishChat: (text: string) =>
    fetch("/api/publish/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    }),

  // ---- CoT/TAK server connections (CRUD) ----
  servers: () => json<{ servers: ServerConnection[] }>("/api/servers"),
  createServer: (body: ServerInput) => jsonBody<{ servers: ServerConnection[] }>("/api/servers", "POST", body),
  updateServer: (id: string, body: ServerInput) =>
    jsonBody<{ servers: ServerConnection[] }>(`/api/servers/${id}`, "PUT", body),
  deleteServer: (id: string) => jsonBody<{ servers: ServerConnection[] }>(`/api/servers/${id}`, "DELETE"),
  testServer: (body: ServerInput) => jsonBody<{ ok: boolean; detail: string }>("/api/servers/test", "POST", body),
  serverTraffic: (server?: string, limit = 200) =>
    json<CotLog>(
      `/api/servers/log?limit=${limit}${server ? `&server=${encodeURIComponent(server)}` : ""}`
    ),
  importPkcs12: (p12_base64: string, password: string) =>
    jsonBody<{ client_cert: string; client_key: string; cacert: string | null; common_name: string }>(
      "/api/servers/pkcs12",
      "POST",
      { p12_base64, password }
    ),

  // ---- built-in ATAK/EUD TAK server ----
  eud: () => json<EudStatus>("/api/eud"),
  setEud: (body: { enabled: boolean; host?: string; port: number; advertised_host?: string | null }) =>
    jsonBody<EudStatus>("/api/eud", "PUT", body),
  eudTraffic: (limit = 200) => json<CotLog>(`/api/eud/log?limit=${limit}`),

  // delete all buffered/logged messages (audit log, telemetry cache, dedup, CoT logs)
  purge: () => jsonBody<{ cleared: Record<string, number>; total: number }>("/api/purge", "POST"),
};

async function jsonBody<T>(url: string, method: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    credentials: "same-origin",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  // Login itself 401s on bad credentials — let the caller surface that, don't bounce to the gate.
  if (res.status === 401 && !url.startsWith("/api/auth/")) onUnauthorized();
  if (!res.ok) throw new Error(`${method} ${url}: ${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

/** A live event stream that reconnects itself if the backend restarts or drops. */
export interface EventStream {
  close(): void;
}

export function openEventStream(onEvent: (e: MessageEvent) => void): EventStream {
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws/events`;
  let ws: WebSocket | null = null;
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const connect = () => {
    if (stopped) return;
    ws = new WebSocket(url);
    ws.onmessage = (m) => onEvent(JSON.parse(m.data) as MessageEvent);
    // on close (backend restart, network blip) retry after a short backoff
    ws.onclose = () => {
      if (!stopped) timer = setTimeout(connect, 1500);
    };
    ws.onerror = () => ws?.close();
  };
  connect();

  return {
    close() {
      stopped = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    },
  };
}

// ---- classification helpers (shared by banner, badges, peers) ----
export const CLASS_LABELS: Record<number, string> = {
  0: "NATO UNCLASSIFIED",
  1: "NATO RESTRICTED",
  2: "NATO CONFIDENTIAL",
  3: "NATO SECRET",
};
export const CLASS_SHORT: Record<number, string> = {
  0: "UNCLAS",
  1: "RESTR",
  2: "CONF",
  3: "SECRET",
};
export function classColor(level: number): string {
  return ["#3aa76d", "#3a78c2", "#d29a2b", "#c0392b"][level] ?? "#7c8a99";
}
