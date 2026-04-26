import { useEffect } from "react";

import { isDashboardGuiEnabled } from "@/lib/dashboard-flags";

declare global {
  interface Window {
    __TAURI__?: {
      notification?: {
        isPermissionGranted: () => Promise<boolean>;
        requestPermission: () => Promise<"default" | "denied" | "granted">;
        sendNotification: (notification: {
          body?: string;
          title: string;
        }) => void;
      };
    };
  }
}

export function DesktopBridge() {
  useEffect(() => {
    if (!isDashboardGuiEnabled()) return;

    const notify = async (title: string, body?: string) => {
      const api = window.__TAURI__?.notification;
      if (!api) return;

      let granted = await api.isPermissionGranted();
      if (!granted) {
        granted = (await api.requestPermission()) === "granted";
      }
      if (granted) api.sendNotification({ body, title });
    };

    const onNotify = (event: Event) => {
      const detail = (event as CustomEvent<{ body?: string; title?: string }>)
        .detail;
      if (!detail?.title) return;
      void notify(detail.title, detail.body);
    };

    window.addEventListener("hermes:desktop-notify", onNotify);
    return () => window.removeEventListener("hermes:desktop-notify", onNotify);
  }, []);

  return null;
}
