import { RotateCw } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { isDashboardGuiEnabled } from "@/lib/dashboard-flags";
import { cn } from "@/lib/utils";

type RuntimeState = "checking" | "healthy" | "reconnecting";

const POLL_MS = 2_500;

export function RuntimeOverlay() {
  const [state, setState] = useState<RuntimeState>("checking");
  const [isGui, setIsGui] = useState(() => isDashboardGuiEnabled());
  const [lastOkAt, setLastOkAt] = useState<number | null>(null);
  const [notifiedDown, setNotifiedDown] = useState(false);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const runtime = await api.getRuntime();
        if (cancelled) return;
        setIsGui(runtime.gui);
        setLastOkAt(Date.now());
        if (notifiedDown) {
          window.dispatchEvent(
            new CustomEvent("hermes:desktop-notify", {
              detail: {
                body: "The dashboard runtime is healthy again.",
                title: "Hermes Reconnected",
              },
            }),
          );
          setNotifiedDown(false);
        }
        setState("healthy");
      } catch {
        if (cancelled) return;
        setNotifiedDown((already) => {
          if (!already && isGui) {
            window.dispatchEvent(
              new CustomEvent("hermes:desktop-notify", {
                detail: {
                  body: "Trying to reconnect to the local Hermes runtime.",
                  title: "Hermes Runtime Disconnected",
                },
              }),
            );
          }
          return true;
        });
        setState((prev) => (prev === "checking" ? "checking" : "reconnecting"));
      }
    };

    void poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, [isGui, notifiedDown]);

  const detail = useMemo(() => {
    if (state === "checking") return "Checking local Hermes runtime...";
    if (!lastOkAt) return "Trying to reconnect to the local Hermes runtime.";
    return `Runtime connection dropped. Last healthy ${Math.max(
      1,
      Math.round((Date.now() - lastOkAt) / 1000),
    )}s ago.`;
  }, [lastOkAt, state]);

  if (!isGui || state === "healthy") return null;

  return (
    <div
      className={cn(
        "fixed inset-0 z-80 flex items-center justify-center",
        "bg-black/70 backdrop-blur-sm",
      )}
      role="status"
      aria-live="polite"
    >
      <div
        className={cn(
          "w-[min(92vw,28rem)] border border-current/20 bg-background-base/95",
          "px-6 py-5 text-midground shadow-2xl",
        )}
      >
        <div className="flex items-start gap-3">
          <RotateCw className="mt-0.5 h-4 w-4 shrink-0 animate-spin" />
          <div className="min-w-0">
            <p className="font-mondwest text-sm tracking-[0.16em]">
              Hermes GUI Runtime
            </p>
            <p className="mt-2 text-xs normal-case leading-5 text-muted-foreground">
              {detail}
            </p>
          </div>
        </div>

        <Button
          type="button"
          variant="outline"
          size="sm"
          className="mt-5 h-8 text-xs"
          onClick={() => window.location.reload()}
        >
          Reload Window
        </Button>
      </div>
    </div>
  );
}
