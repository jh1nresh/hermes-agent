import { Markdown } from "@/components/Markdown";
import { ModelPickerDialog } from "@/components/ModelPickerDialog";
import {
  SlashPopover,
  type SlashPopoverHandle,
} from "@/components/SlashPopover";
import { ToolCall, type ToolEntry } from "@/components/ToolCall";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { GatewayClient, type ConnectionState } from "@/lib/gatewayClient";
import { executeSlash } from "@/lib/slashExec";
import {
  AlertCircle,
  ChevronDown,
  Copy,
  Heart,
  RefreshCw,
  Send,
  Square,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";

/*
 * Chat — the "Ink TUI in a browser" proof.
 *
 * Drives the exact same tui_gateway JSON-RPC surface Ink drives over stdio,
 * but over a WebSocket served by hermes_cli/web_server.py. Covers message
 * streaming, tool calls, interrupts, slash commands, and model switching.
 * Approvals / clarify / resume picker / attachments are still TODO; the
 * event listeners on GatewayClient give type-safe hooks for each.
 */

type MessageRole = "user" | "assistant" | "system";

interface TextMessage {
  kind: "message";
  id: string;
  role: MessageRole;
  text: string;
  streaming?: boolean;
  rendered?: string;
  error?: boolean;
}

type ChatEntry = TextMessage | ToolEntry;

/** Shape of messages returned by session.resume — see _history_to_messages in tui_gateway/server.py. */
interface HydratedMessage {
  role: "user" | "assistant" | "system" | "tool";
  text?: string;
  name?: string;
  context?: string;
}

interface SessionResumeResponse {
  session_id: string;
  resumed: string;
  message_count: number;
  messages: HydratedMessage[];
  info?: Record<string, unknown>;
}

interface SessionInfo {
  model?: string;
  provider?: string;
  cwd?: string;
  tools?: Record<string, unknown>;
  skills?: Record<string, unknown>;
  credential_warning?: string;
}

const STATE_LABEL: Record<ConnectionState, string> = {
  idle: "idle",
  connecting: "connecting",
  open: "connected",
  closed: "closed",
  error: "error",
};

const STATE_TONE: Record<ConnectionState, string> = {
  idle: "bg-muted text-muted-foreground",
  connecting: "bg-primary/10 text-primary",
  open: "bg-emerald-500/10 text-emerald-500 dark:text-emerald-400",
  closed: "bg-muted text-muted-foreground",
  error: "bg-destructive/10 text-destructive",
};

const randId = (prefix: string) =>
  `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

// Mirror ui-tui/src/app/useMainApp.ts — same regex, same palette, same beat.
// Web parity with the Ink TUI's GoodVibesHeart easter egg: a thank-you pulses
// a heart next to the connection badge.
const GOOD_VIBES_RE = /\b(good bot|thanks|thank you|thx|ty|ily|love you)\b/i;
const HEART_COLORS = ["#ff5fa2", "#ff4d6d", "#ffbd38"];

export default function ChatPage() {
  const gwRef = useRef<GatewayClient | null>(null);
  const slashRef = useRef<SlashPopoverHandle | null>(null);
  const transcriptEndRef = useRef<HTMLDivElement | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);

  const [searchParams] = useSearchParams();
  const resumeId = searchParams.get("resume") ?? "";

  const [connState, setConnState] = useState<ConnectionState>("idle");
  const [sessionId, setSessionId] = useState("");
  const [sessionInfo, setSessionInfo] = useState<SessionInfo | null>(null);
  const [entries, setEntries] = useState<ChatEntry[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [connectError, setConnectError] = useState("");
  const [runtimeError, setRuntimeError] = useState("");
  const [modelPickerOpen, setModelPickerOpen] = useState(false);
  const [goodVibesTick, setGoodVibesTick] = useState(0);

  /* ---------------------------------------------------------------- */
  /*  Entry helpers                                                    */
  /* ---------------------------------------------------------------- */

  /** Replace the most recent streaming assistant message, if any. */
  const updateStreamingAssistant = useCallback(
    (fn: (m: TextMessage) => TextMessage) => {
      setEntries((list) => {
        for (let i = list.length - 1; i >= 0; i--) {
          const e = list[i];
          if (e.kind === "message" && e.role === "assistant" && e.streaming) {
            const next = list.slice();
            next[i] = fn(e);
            return next;
          }
        }
        return list;
      });
    },
    [],
  );

  const pushMessage = useCallback(
    (role: MessageRole, text: string, extra: Partial<TextMessage> = {}) => {
      setEntries((list) => [
        ...list,
        { kind: "message", id: randId(role[0]), role, text, ...extra },
      ]);
    },
    [],
  );

  const pushSystem = useCallback(
    (text: string) => pushMessage("system", text),
    [pushMessage],
  );

  /* ---------------------------------------------------------------- */
  /*  Bootstrap: connect, wire events, open or resume a session        */
  /* ---------------------------------------------------------------- */

  const bootstrap = useCallback(async () => {
    setEntries([]);
    setSessionId("");
    setSessionInfo(null);
    setBusy(false);
    setConnectError("");
    setRuntimeError("");

    const gw = gwRef.current ?? new GatewayClient();
    gwRef.current = gw;

    gw.onState(setConnState);

    gw.on<SessionInfo>("session.info", (ev) => {
      if (ev.payload) setSessionInfo(ev.payload);
    });

    gw.on("message.start", () => {
      pushMessage("assistant", "", { streaming: true });
      setBusy(true);
    });

    gw.on<{ text?: string; rendered?: string }>("message.delta", (ev) => {
      const d = ev.payload?.text ?? "";
      if (!d) return;
      updateStreamingAssistant((m) => ({ ...m, text: m.text + d }));
    });

    gw.on<{ text?: string; rendered?: string; reasoning?: string }>(
      "message.complete",
      (ev) => {
        updateStreamingAssistant((m) => ({
          ...m,
          text: ev.payload?.text ?? m.text,
          rendered: ev.payload?.rendered,
          streaming: false,
        }));
        setBusy(false);
      },
    );

    gw.on<{ tool_id: string; name?: string; context?: string }>(
      "tool.start",
      (ev) => {
        if (!ev.payload) return;
        const { tool_id, name, context } = ev.payload;

        // Insert tool rows BEFORE the current streaming assistant bubble so
        // the transcript reads "user → tools → final message" rather than
        // "empty bubble → tool → bubble filling in". If there's no streaming
        // assistant (tool fired before message.start, or no message at all),
        // append to the end.
        const row: ToolEntry = {
          kind: "tool",
          id: `t-${tool_id}`,
          tool_id,
          name: name ?? "tool",
          context,
          status: "running",
          startedAt: Date.now(),
        };

        setEntries((list) => {
          for (let i = list.length - 1; i >= 0; i--) {
            const e = list[i];
            if (e.kind === "message" && e.role === "assistant" && e.streaming) {
              return [...list.slice(0, i), row, ...list.slice(i)];
            }
          }
          return [...list, row];
        });
      },
    );

    gw.on<{ name?: string; preview?: string }>("tool.progress", (ev) => {
      const name = ev.payload?.name ?? "";
      const preview = ev.payload?.preview ?? "";
      if (!name || !preview) return;

      // Update the most recent running tool entry with this name.
      setEntries((list) => {
        for (let i = list.length - 1; i >= 0; i--) {
          const e = list[i];
          if (e.kind === "tool" && e.status === "running" && e.name === name) {
            const next = list.slice();
            next[i] = { ...e, preview };
            return next;
          }
        }
        return list;
      });
    });

    gw.on<{
      tool_id: string;
      name?: string;
      summary?: string;
      error?: string;
      inline_diff?: string;
    }>("tool.complete", (ev) => {
      if (!ev.payload) return;
      const { tool_id, summary, error, inline_diff } = ev.payload;

      setEntries((list) =>
        list.map((e) =>
          e.kind === "tool" && e.tool_id === tool_id
            ? {
                ...e,
                status: error ? "error" : "done",
                summary: summary ?? (error ? undefined : e.summary),
                error: error ?? e.error,
                inline_diff: inline_diff ?? e.inline_diff,
                completedAt: Date.now(),
              }
            : e,
        ),
      );
    });

    gw.on<{ message?: string }>("error", (ev) => {
      setRuntimeError(ev.payload?.message ?? "unknown error");
      setBusy(false);
    });

    try {
      await gw.connect();

      if (resumeId) {
        const resp = await gw.request<SessionResumeResponse>("session.resume", {
          session_id: resumeId,
          cols: 100,
        });
        setSessionId(resp.session_id);
        setEntries(hydrateMessages(resp.messages ?? []));
        pushSystem(
          `resumed session ${resp.resumed} · ${resp.message_count ?? resp.messages?.length ?? 0} messages`,
        );
        // NOTE: intentionally NOT clearing the ?resume= param. Doing so
        // flips `resumeId` back to "" which is a dep of the bootstrap
        // effect, re-triggering cleanup + a fresh session.create and
        // wiping the transcript we just hydrated.
      } else {
        const { session_id } = await gw.request<{ session_id: string }>(
          "session.create",
          { cols: 100 },
        );
        setSessionId(session_id);
      }
    } catch (err) {
      setConnectError(err instanceof Error ? err.message : String(err));
    }
  }, [pushMessage, pushSystem, resumeId, updateStreamingAssistant]);

  // Rebootstrap whenever the resume target changes. React Router keeps the
  // component mounted when the search params flip, so navigating to
  // /chat?resume=X from within the app must tear down the current WS
  // connection and open a fresh session.
  useEffect(() => {
    bootstrap();
    return () => {
      gwRef.current?.close();
      gwRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [resumeId]);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({
      behavior: "smooth",
      block: "end",
    });
  }, [entries]);

  /* ---------------------------------------------------------------- */
  /*  Submission                                                       */
  /* ---------------------------------------------------------------- */

  const submitUserMessage = useCallback(
    async (text: string) => {
      const gw = gwRef.current;
      const trimmed = text.trim();
      if (!gw || !sessionId || !trimmed) return;

      pushMessage("user", trimmed);
      setRuntimeError("");

      try {
        await gw.request("prompt.submit", {
          session_id: sessionId,
          text: trimmed,
        });
      } catch (err) {
        setRuntimeError(err instanceof Error ? err.message : String(err));
        setBusy(false);
        updateStreamingAssistant((m) => ({
          ...m,
          streaming: false,
          error: true,
        }));
      }
    },
    [sessionId, pushMessage, updateStreamingAssistant],
  );

  const submitSlash = useCallback(
    async (command: string) => {
      const gw = gwRef.current;
      if (!gw || !sessionId) return;

      pushSystem(command);
      await executeSlash({
        command,
        sessionId,
        gw,
        callbacks: { sys: pushSystem, send: submitUserMessage },
      });
    },
    [sessionId, pushSystem, submitUserMessage],
  );

  const send = useCallback(async () => {
    const text = draft.trim();
    if (!text || busy || !sessionId) return;

    setDraft("");
    if (!text.startsWith("/") && GOOD_VIBES_RE.test(text)) {
      setGoodVibesTick((v) => v + 1);
    }
    await (text.startsWith("/") ? submitSlash(text) : submitUserMessage(text));
  }, [busy, draft, sessionId, submitSlash, submitUserMessage]);

  const interrupt = useCallback(() => {
    gwRef.current
      ?.request("session.interrupt", { session_id: sessionId })
      .catch(() => {
        /* resync on next status event */
      });
  }, [sessionId]);

  /* ---------------------------------------------------------------- */
  /*  Render                                                           */
  /* ---------------------------------------------------------------- */

  const canSend =
    connState === "open" && !!sessionId && !busy && draft.trim().length > 0;
  const canPickModel = connState === "open" && !!sessionId;
  const placeholder =
    connState !== "open"
      ? "waiting for gateway…"
      : busy
        ? "agent is running — press Interrupt to stop, or queue a follow-up"
        : "message hermes… (Enter to send, Shift+Enter for newline, / for commands)";

  return (
    // Opt out of the App root's `font-mondwest uppercase` — the dashboard
    // uses pixel-display caps for chrome, but chat prose needs readable
    // mixed-case. `font-courier` matches the terminal aesthetic without
    // fighting the rest of the app's typography.
    <div className="flex flex-col gap-4 h-[calc(100vh-8rem)] font-courier normal-case">
      <header className="flex flex-wrap items-center gap-2 justify-between">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge className={STATE_TONE[connState]}>
            <span className="mr-1 h-1.5 w-1.5 rounded-full bg-current inline-block" />
            {STATE_LABEL[connState]}
          </Badge>

          <GoodVibesHeart tick={goodVibesTick} />

          <ModelBadge
            model={sessionInfo?.model}
            enabled={canPickModel}
            onClick={() => setModelPickerOpen(true)}
          />

          {sessionId && (
            <button
              onClick={() =>
                navigator.clipboard?.writeText(sessionId).catch(() => {})
              }
              className="inline-flex items-center gap-1 font-mono text-[0.7rem] text-muted-foreground hover:text-foreground transition-colors cursor-pointer"
              title="Copy session id"
            >
              <Copy className="h-3 w-3" />
              {sessionId}
            </button>
          )}
        </div>

        <div className="flex items-center gap-2">
          {busy && (
            <Button onClick={interrupt} variant="outline" size="sm">
              <Square className="h-3 w-3 mr-1" fill="currentColor" />
              Interrupt
            </Button>
          )}

          <Button onClick={bootstrap} variant="ghost" size="sm">
            <RefreshCw className="h-3 w-3 mr-1" />
            Reset session
          </Button>
        </div>
      </header>

      {connectError && (
        <Card className="p-3 border-destructive/50 bg-destructive/5 text-sm flex items-start gap-2">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0 text-destructive" />
          <div>
            <div className="font-medium text-destructive">
              Can't connect to gateway
            </div>
            <div className="text-muted-foreground text-xs mt-0.5">
              {connectError}
            </div>
          </div>
        </Card>
      )}

      <Card className="flex-1 min-h-0 overflow-hidden flex flex-col">
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 space-y-3">
          {entries.length === 0 && !connectError && (
            <EmptyState connState={connState} cwd={sessionInfo?.cwd} />
          )}

          {entries.map((entry) =>
            entry.kind === "tool" ? (
              <ToolCall key={entry.id} tool={entry} />
            ) : (
              <MessageRow key={entry.id} message={entry} />
            ),
          )}

          {runtimeError && (
            <div className="flex items-start gap-2 text-xs text-destructive">
              <AlertCircle className="h-3.5 w-3.5 mt-0.5 shrink-0" />
              <span>{runtimeError}</span>
            </div>
          )}

          <div ref={transcriptEndRef} />
        </div>

        <div className="border-t border-border p-3 sm:p-4 relative">
          <SlashPopover
            ref={slashRef}
            input={draft}
            gw={gwRef.current}
            onApply={(next) => {
              setDraft(next);
              textareaRef.current?.focus();
            }}
          />

          <div className="flex items-stretch overflow-hidden rounded-md border border-border bg-background/40 transition-colors focus-within:border-foreground/30 focus-within:bg-background/60 focus-within:ring-1 focus-within:ring-foreground/20">
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (slashRef.current?.handleKey(e)) return;
                if (
                  e.key === "Enter" &&
                  !e.shiftKey &&
                  !e.nativeEvent.isComposing
                ) {
                  e.preventDefault();
                  send();
                }
              }}
              placeholder={placeholder}
              rows={1}
              className="flex-1 resize-none bg-transparent px-3.5 py-2.5 text-sm leading-relaxed placeholder:text-muted-foreground/50 focus:outline-none min-h-[40px] max-h-[200px] disabled:opacity-50"
              style={{ fieldSizing: "content" } as React.CSSProperties}
              disabled={connState !== "open"}
            />

            <button
              type="button"
              onClick={send}
              disabled={!canSend}
              aria-label="Send message"
              className="shrink-0 w-11 flex items-center justify-center border-l border-border bg-foreground/90 text-background transition-colors cursor-pointer hover:bg-foreground active:bg-foreground/80 disabled:opacity-30 disabled:cursor-not-allowed disabled:hover:bg-foreground/90"
            >
              <Send className="h-4 w-4" />
            </button>
          </div>
        </div>
      </Card>

      {modelPickerOpen && gwRef.current && (
        <ModelPickerDialog
          gw={gwRef.current}
          sessionId={sessionId}
          onClose={() => setModelPickerOpen(false)}
          onSubmit={submitSlash}
        />
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Subcomponents                                                      */
/* ------------------------------------------------------------------ */

/**
 * Port of ui-tui's GoodVibesHeart — a ♥ glows for 650ms in a random palette
 * colour every time the user says something kind. Same regex, same beat, just
 * rendered via a Lucide icon instead of an Ink Text node.
 */
function GoodVibesHeart({ tick }: { tick: number }) {
  const [active, setActive] = useState(false);
  const [color, setColor] = useState(HEART_COLORS[0]);

  useEffect(() => {
    if (tick <= 0) return;
    setColor(HEART_COLORS[Math.floor(Math.random() * HEART_COLORS.length)]);
    setActive(true);
    const id = setTimeout(() => setActive(false), 650);
    return () => clearTimeout(id);
  }, [tick]);

  return (
    <Heart
      aria-hidden
      className={`h-4 w-4 transition-all duration-300 ${
        active ? "scale-125 opacity-100" : "scale-75 opacity-0"
      }`}
      fill={active ? color : "none"}
      style={{ color }}
    />
  );
}

function ModelBadge({
  model,
  enabled,
  onClick,
}: {
  model: string | undefined;
  enabled: boolean;
  onClick(): void;
}) {
  const hasModel = !!model;
  const className = hasModel
    ? "inline-flex items-center gap-1 rounded-md border border-border bg-muted/40 px-2 py-0.5 font-mono text-[0.7rem] hover:bg-muted hover:border-foreground/30 transition-colors cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
    : "inline-flex items-center gap-1 rounded-md border border-dashed border-border px-2 py-0.5 font-mono text-[0.7rem] text-muted-foreground hover:text-foreground hover:border-foreground/30 transition-colors cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed";

  return (
    <button
      type="button"
      onClick={() => enabled && onClick()}
      disabled={!enabled}
      title="Click to switch model (same as /model)"
      className={className}
    >
      {hasModel ? (
        <>
          <span>{model}</span>
          <ChevronDown className="h-3 w-3 text-muted-foreground" />
        </>
      ) : (
        <>
          <ChevronDown className="h-3 w-3" />
          pick model
        </>
      )}
    </button>
  );
}

function EmptyState({
  connState,
  cwd,
}: {
  connState: ConnectionState;
  cwd: string | undefined;
}) {
  const ready = connState === "open";

  return (
    <div className="h-full flex items-center justify-center text-center px-4">
      <div className="max-w-md space-y-4">
        <div className="text-base text-foreground/80">
          {ready ? (
            <>
              hermes is ready
              <span className="ml-0.5 inline-block w-1.5 h-4 bg-foreground/60 align-middle animate-pulse" />
            </>
          ) : (
            "connecting to gateway…"
          )}
        </div>

        <div className="text-xs text-muted-foreground/70 leading-relaxed">
          same agent, same tools — served over a socket.
        </div>

        <div className="flex flex-wrap justify-center items-center gap-1.5 text-[0.7rem] text-muted-foreground/60 pt-1">
          <span>type</span>
          <kbd className="rounded border border-border bg-muted/40 px-1.5 py-0.5 font-mono">
            /
          </kbd>
          <span>for slash commands,</span>
          <kbd className="rounded border border-border bg-muted/40 px-1.5 py-0.5 font-mono">
            Enter
          </kbd>
          <span>to send</span>
        </div>

        {cwd && (
          <div className="pt-2 font-mono text-[0.65rem] text-muted-foreground/40 truncate">
            cwd · {cwd}
          </div>
        )}
      </div>
    </div>
  );
}

function MessageRow({ message }: { message: TextMessage }) {
  if (message.role === "user") {
    return (
      <div className="flex justify-end">
        <div className="max-w-[80%] rounded-lg bg-primary text-primary-foreground px-3 py-2 whitespace-pre-wrap text-sm">
          {message.text}
        </div>
      </div>
    );
  }

  if (message.role === "system") {
    return (
      <div className="flex justify-center">
        <div className="max-w-full rounded-md border border-dashed border-border bg-muted/20 px-3 py-1.5 text-xs text-muted-foreground font-mono whitespace-pre-wrap">
          {message.text}
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start">
      <div
        className={`max-w-[85%] rounded-lg border px-3.5 py-2.5 ${
          message.error
            ? "border-destructive/50 bg-destructive/5"
            : "border-border bg-muted/30"
        }`}
      >
        {message.text ? (
          <Markdown content={message.text} streaming={message.streaming} />
        ) : (
          <span className="inline-flex items-center gap-1 text-muted-foreground text-sm italic">
            thinking…
            {message.streaming && (
              <span
                aria-hidden
                className="inline-block w-[0.5em] h-[1em] align-[-0.15em] bg-foreground/50 animate-pulse"
              />
            )}
          </span>
        )}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Hydration                                                          */
/* ------------------------------------------------------------------ */

function hydrateMessages(list: HydratedMessage[]): ChatEntry[] {
  return list.map(
    (m, i): ChatEntry =>
      m.role === "tool"
        ? {
            kind: "tool",
            id: `h-tool-${i}`,
            tool_id: `h-tool-${i}`,
            name: m.name ?? "tool",
            context: m.context || undefined,
            status: "done",
            // Historical — no reliable timestamps in the hydrated payload.
            startedAt: 0,
          }
        : {
            kind: "message",
            id: `h-msg-${i}`,
            role: m.role,
            text: m.text ?? "",
          },
  );
}
