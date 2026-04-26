declare global {
  interface Window {
    /** Set true by the server only for `hermes dashboard --tui` (or HERMES_DASHBOARD_TUI=1). */
    __HERMES_DASHBOARD_EMBEDDED_CHAT__?: boolean;
    /** Set true by the server for `hermes dashboard --gui`. */
    __HERMES_DASHBOARD_GUI__?: boolean;
    /** @deprecated Older injected name; treated as on when true. */
    __HERMES_DASHBOARD_TUI__?: boolean;
  }
}

/** True only when the dashboard was started with embedded TUI Chat (`hermes dashboard --tui`). */
export function isDashboardEmbeddedChatEnabled(): boolean {
  if (typeof window === "undefined") return false;
  if (window.__HERMES_DASHBOARD_EMBEDDED_CHAT__ === true) return true;
  return window.__HERMES_DASHBOARD_TUI__ === true;
}

export function isDashboardGuiEnabled(): boolean {
  if (typeof window === "undefined") return false;
  return window.__HERMES_DASHBOARD_GUI__ === true;
}
