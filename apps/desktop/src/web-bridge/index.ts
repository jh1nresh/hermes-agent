/**
 * The web implementation of `window.hermesDesktop` — the bridge the desktop
 * renderer uses for every platform service (RFC docs/plans/2026-07-10-001,
 * Phase 1).
 *
 * Under Electron the preload script installs the real IPC bridge before any
 * renderer code runs, so this module is inert there. In a plain browser
 * (`/app` served by the gateway, or `vite dev` with the HERMES_SPIKE_BACKEND
 * proxy) no preload exists, and main.tsx installs this bridge instead.
 *
 * Design rules:
 *  - Same-origin only. Every REST call and WebSocket targets the page origin;
 *    auth mirrors web/src/lib/api.ts exactly (the dashboard SPA, which is the
 *    proven browser client of this backend):
 *      · gated / OAuth mode (`window.__HERMES_AUTH_REQUIRED__`): HttpOnly
 *        session cookie rides on `credentials: 'include'`; WS upgrades mint a
 *        single-use ticket at POST /api/auth/ws-ticket.
 *      · loopback / token mode: the gateway injects
 *        `window.__HERMES_SESSION_TOKEN__` into index.html; REST sends it as
 *        X-Hermes-Session-Token, WS appends `?token=`.
 *  - The synthesized connection reports `mode: 'remote'` (RFC D4). That flips
 *    isDesktopFsRemoteMode() and every remote-mode branch, so fs/git/session
 *    surfaces route to the backend's filesystem through the same code paths
 *    the Electron app uses against a remote gateway.
 *  - Everything Electron-only returns an inert value and is listed in
 *    WEB_STUBBED_SURFACE below. The parity test (parity.test.ts) fails when a
 *    new preload method is neither implemented nor registered there, so the
 *    two bridges cannot drift silently.
 */
import { buildHermesWebSocketUrl } from '@hermes/shared'

type HermesDesktopBridge = NonNullable<Window['hermesDesktop']>

/**
 * Preload methods that exist on web only as inert stubs, with the reason.
 * Grow this list deliberately — each entry is a product decision, not a
 * shrug. The parity test enforces membership.
 */
export const WEB_STUBBED_SURFACE: Record<string, string> = {
  applyConnectionConfig: 'connection is fixed to the serving origin',
  cancelBootstrap: 'no first-launch bootstrap in a browser',
  cloud: 'the browser reaches agents via the portal, not embedded discovery',
  fetchLinkTitle: 'needs a cross-origin fetch proxy; PrettyLink falls back to the URL',
  getRecentLogs: 'main-process log buffer does not exist on web',
  normalizePreviewTarget: 'main-process path/URL normalization; preview falls back',
  notify: 'delivered via the Notification API (permission requested at first use)',
  oauthLoginConnectionConfig: 'sign-in happens on the gateway login page itself',
  oauthLogoutConnectionConfig: 'sign-out happens on the gateway login page itself',
  onBackendExit: 'no child process to observe',
  onBootProgress: 'no main-process boot pipeline; snapshot comes from getBootProgress',
  onBootstrapEvent: 'no first-launch bootstrap in a browser',
  onPreviewFileChanged: 'no file watcher on web (Phase 2 candidate)',
  probeConnectionConfig: 'connection is fixed to the serving origin',
  profile: 'a hosted instance serves one profile',
  repairBootstrap: 'no first-launch bootstrap in a browser',
  requestMicrophoneAccess: 'getUserMedia prompts at point of use',
  resetBootstrap: 'no first-launch bootstrap in a browser',
  revealLogs: 'no local log directory to reveal',
  saveConnectionConfig: 'connection is fixed to the serving origin',
  selectPaths: 'native file picker; gateway-fs picker is Phase 2',
  settings: 'default project dir is a local-machine concept',
  stopPreviewFileWatch: 'no file watcher on web (Phase 2 candidate)',
  testConnectionConfig: 'connection is fixed to the serving origin',
  watchPreviewFile: 'no file watcher on web (Phase 2 candidate)'
}

/**
 * Preload methods deliberately ABSENT from the web bridge. Every entry is
 * optional in the `Window['hermesDesktop']` type and every caller guards with
 * `?.`, so absence cleanly disables the feature (the honest signal — no inert
 * function pretending to work). The parity test enforces that each entry
 * really is missing from the web bridge and really does exist on preload.
 */
export const WEB_OMITTED_SURFACE: Record<string, string> = {
  git: 'remote mode routes desktop-git.ts to /api/git/* (remoteGit); the Electron-local branch is unused',
  onClosePreviewRequested: 'no app menu to emit it',
  onConnectionApplied: 'connection cannot be soft-switched from the server side',
  onDeepLink: 'no hermes:// protocol handler in a browser tab',
  onFocusSession: 'no cross-window notification routing',
  onNotificationAction: 'Notification API actions are not wired (Phase 2 with real notify)',
  onOpenUpdatesRequested: 'no app menu to emit it',
  onPowerResume: "browsers have no resume signal; the boot path's online/visibilitychange listeners cover wake",
  onWindowStateChanged: 'no native window chrome to report',
  petOverlay: 'no OS overlay windows in a browser; the overlay app never mounts (?win=overlay is Electron-launched)',
  renamePath: 'no gateway rename endpoint yet; project-tree rename hides',
  revealPath: 'no OS file manager to reveal into',
  setNativeTheme: 'no native window chrome to theme',
  setPreviewShortcutActive: 'no global shortcut registration',
  setTitleBarTheme: 'no native title bar',
  setTranslucency: 'no compositor-backed window translucency',
  terminal: 'PTY-over-WebSocket lands in Phase 2 (/api/pty); absence renders terminal tabs as closed',
  themes: 'marketplace fetch needs a proxy endpoint; deferred (RFC D6); absence empties the theme search',
  trashPath: 'no OS trash; destructive delete needs its own web decision',
  uninstall: 'nothing installed locally; UninstallSection self-hides without the bridge',
  updates: 'the instance updates server-side; About hides the update controls on web',
  zoom: 'the browser owns page zoom (Ctrl +/-)'
}

// ---------------------------------------------------------------------------
// Auth + transport
// ---------------------------------------------------------------------------

interface WebAuthGlobals {
  __HERMES_AUTH_REQUIRED__?: boolean
  __HERMES_BASE_PATH__?: string
  __HERMES_SESSION_TOKEN__?: string
}

function authGlobals(): WebAuthGlobals {
  return window as WebAuthGlobals
}

/** URL prefix when the gateway is reverse-proxied below a subpath. */
function basePath(): string {
  return authGlobals().__HERMES_BASE_PATH__ ?? ''
}

function isGated(): boolean {
  return Boolean(authGlobals().__HERMES_AUTH_REQUIRED__)
}

/**
 * Loopback/token-mode session token. The gateway injects it into the served
 * index.html; the vite-dev fallback keeps the HERMES_SPIKE_BACKEND proxy
 * workflow usable, where no HTML injection happens.
 */
function sessionToken(): string {
  return authGlobals().__HERMES_SESSION_TOKEN__ ?? import.meta.env.VITE_HERMES_SPIKE_TOKEN ?? ''
}

async function webFetch(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<Response> {
  const headers = new Headers(init?.headers)

  if (!isGated() && !headers.has('X-Hermes-Session-Token')) {
    headers.set('X-Hermes-Session-Token', sessionToken())
  }

  return fetch(`${basePath()}${path}`, {
    ...init,
    credentials: init?.credentials ?? 'include',
    headers,
    signal: AbortSignal.timeout(init?.timeoutMs ?? 30_000)
  })
}

async function webFetchJson<T>(path: string, init?: RequestInit & { timeoutMs?: number }): Promise<T> {
  const res = await webFetch(path, init)

  if (!res.ok) {
    const text = await res.text().catch(() => '')

    throw new Error(`${res.status}: ${text || res.statusText}`)
  }

  return (await res.json()) as T
}

/**
 * Resolve the WS auth query pair. Gated mode mints a fresh single-use ticket
 * (short TTL — callers must mint immediately before opening the socket, which
 * is exactly what resolveGatewayWsUrl() in the boot path does by re-calling
 * getGatewayWsUrl on every connect).
 */
async function wsAuthParam(): Promise<readonly [string, string]> {
  if (isGated()) {
    const { ticket } = await webFetchJson<{ ticket: string }>('/api/auth/ws-ticket', { method: 'POST' })

    return ['ticket', ticket]
  }

  return ['token', sessionToken()]
}

async function gatewayWsUrl(): Promise<string> {
  return buildHermesWebSocketUrl({
    authParam: await wsAuthParam(),
    basePath: basePath(),
    path: '/api/ws'
  })
}

// ---------------------------------------------------------------------------
// Connection synthesis
// ---------------------------------------------------------------------------

function webConnection(): Awaited<ReturnType<HermesDesktopBridge['getConnection']>> {
  return {
    // 'oauth' makes the boot path mint a fresh ticket per (re)connect and
    // routes reauth failures to the sign-in-again flow (GatewayReauthRequired).
    authMode: isGated() ? 'oauth' : 'token',
    baseUrl: `${window.location.origin}${basePath()}`,
    isFullscreen: false,
    logs: [],
    // 'remote' is the keystone (RFC D4): every fs/git/session surface treats
    // the backend as the filesystem of record, exactly like the Electron
    // app's remote-gateway mode.
    mode: 'remote',
    nativeOverlayWidth: 0,
    source: 'settings',
    token: isGated() ? '' : sessionToken(),
    windowButtonPosition: null,
    // Pre-built URL for consumers that read conn.wsUrl directly. In gated
    // mode a baked ticket would be stale by connect time; the boot path
    // always re-mints via getGatewayWsUrl (resolveGatewayWsUrl), so bake the
    // token form only when it is actually valid.
    wsUrl: buildHermesWebSocketUrl({
      authParam: isGated() ? undefined : ['token', sessionToken()],
      basePath: basePath(),
      path: '/api/ws'
    })
  }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const unsubscribe = () => () => {}

function anchorDownload(href: string, filename?: string) {
  const a = document.createElement('a')
  a.href = href
  a.download = filename ?? ''
  a.rel = 'noopener'
  document.body.appendChild(a)
  a.click()
  a.remove()
}

function openTab(url: string): void {
  window.open(url, '_blank', 'noopener,noreferrer')
}

function appUrl(hashRoute: string): string {
  return `${window.location.origin}${window.location.pathname}${hashRoute}`
}

const INACTIVE_BOOTSTRAP: Awaited<ReturnType<HermesDesktopBridge['getBootstrapState']>> = {
  active: false,
  completedAt: null,
  error: null,
  log: [],
  manifest: null,
  stages: {},
  startedAt: null,
  unsupportedPlatform: null
}

// ---------------------------------------------------------------------------
// The bridge
// ---------------------------------------------------------------------------

export function createWebBridge(): HermesDesktopBridge {
  const bridge: HermesDesktopBridge = {
    host: 'web',

    // --- connection + boot -------------------------------------------------
    api: async request => {
      const hasBody = request.body !== undefined

      return webFetchJson(request.path, {
        body: hasBody ? JSON.stringify(request.body) : undefined,
        headers: hasBody ? { 'Content-Type': 'application/json' } : undefined,
        method: request.method ?? 'GET',
        timeoutMs: request.timeoutMs
      })
    },
    getConnection: async () => webConnection(),
    getGatewayWsUrl: async () => gatewayWsUrl(),
    revalidateConnection: async () => ({ ok: true, rebuilt: false }),
    touchBackend: async () => ({ ok: true }),
    getBootProgress: async () => ({
      error: null,
      fakeMode: false,
      message: 'Connecting to gateway',
      phase: 'renderer.boot',
      progress: 90,
      running: true,
      timestamp: Date.now()
    }),
    getConnectionConfig: async () => ({
      cloudOrg: '',
      envOverride: false,
      mode: 'remote',
      profile: null,
      remoteAuthMode: isGated() ? 'oauth' : 'token',
      remoteOauthConnected: isGated(),
      remoteTokenPreview: null,
      remoteTokenSet: !isGated(),
      remoteUrl: `${window.location.origin}${basePath()}`
    }),
    getVersion: async () => ({
      // Vite-define'd renderer package version; guarded so a non-vite harness
      // (node --test, plain vitest transform) doesn't throw on the bare const.
      appVersion: typeof __HERMES_RENDERER_VERSION__ === 'string' ? __HERMES_RENDERER_VERSION__ : 'web',
      electronVersion: 'web',
      hermesRoot: '',
      nodeVersion: 'web',
      platform: 'web'
    }),
    getRemoteDisplayReason: async () => null,
    sanitizeWorkspaceCwd: async cwd => ({ cwd: cwd ?? '', sanitized: false }),

    // --- windows (browser tabs) --------------------------------------------
    openSessionWindow: async sessionId => {
      const id = String(sessionId ?? '').trim()

      if (!id) {
        return { error: 'invalid-session', ok: false }
      }

      openTab(appUrl(`#/${encodeURIComponent(id)}`))

      return { ok: true }
    },
    openNewSessionWindow: async () => {
      openTab(appUrl('#/'))

      return { ok: true }
    },
    openExternal: async url => {
      openTab(url)
    },
    openPreviewInBrowser: async url => {
      openTab(url)
    },
    signalDeepLinkReady: async () => ({ ok: true }),

    // --- clipboard / files / media ------------------------------------------
    writeClipboard: async text => {
      await navigator.clipboard.writeText(text)

      return true
    },
    getPathForFile: () => '',
    readFileDataUrl: async filePath => {
      const { dataUrl } = await webFetchJson<{ dataUrl: string }>(
        `/api/fs/read-data-url?path=${encodeURIComponent(filePath)}`
      )

      return dataUrl
    },
    readFileText: async filePath =>
      webFetchJson(`/api/fs/read-text?path=${encodeURIComponent(filePath)}`),
    readDir: async dirPath => {
      try {
        return await webFetchJson(`/api/fs/list?path=${encodeURIComponent(dirPath)}`)
      } catch (error) {
        return { entries: [], error: error instanceof Error ? error.message : 'read-error' }
      }
    },
    gitRoot: async startPath => {
      const { root } = await webFetchJson<{ root: null | string }>(
        `/api/fs/git-root?path=${encodeURIComponent(startPath)}`
      )

      return root
    },
    writeTextFile: async (filePath, content) =>
      webFetchJson('/api/fs/write-text', {
        body: JSON.stringify({ content, path: filePath }),
        headers: { 'Content-Type': 'application/json' },
        method: 'POST'
      }),
    saveImageFromUrl: async url => {
      anchorDownload(url)

      return true
    },
    saveImageBuffer: async (data, ext) => {
      const blob = new Blob([data instanceof Uint8Array ? (data as Uint8Array<ArrayBuffer>) : data])
      const href = URL.createObjectURL(blob)
      const filename = `hermes-image-${Date.now()}.${ext.replace(/^\./, '')}`
      anchorDownload(href, filename)
      setTimeout(() => URL.revokeObjectURL(href), 30_000)

      return filename
    },
    saveClipboardImage: async () => {
      // Composer paste path: the bytes must land on the BACKEND (vision needs
      // them; the browser page has no local path), so stage through the same
      // upload endpoint the dashboard chat uses and hand back the
      // gateway-visible path.
      try {
        const items = await navigator.clipboard.read()

        for (const item of items) {
          const mime = item.types.find(t => t.startsWith('image/'))

          if (!mime) {
            continue
          }

          const blob = await item.getType(mime)

          const dataUrl = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader()
            reader.onload = () => resolve(String(reader.result))
            reader.onerror = () => reject(reader.error)
            reader.readAsDataURL(blob)
          })

          const { path } = await webFetchJson<{ path: string }>('/api/chat/image-upload', {
            body: JSON.stringify({ data_url: dataUrl }),
            headers: { 'Content-Type': 'application/json' },
            method: 'POST'
          })

          return path
        }
      } catch {
        // Permission denied or no image on the clipboard — same "nothing to
        // paste" result either way.
      }

      return ''
    },

    // --- boot-blocking bootstrap snapshot (spike pitfall #1) -----------------
    getBootstrapState: async () => INACTIVE_BOOTSTRAP,

    // --- inert surface (see WEB_STUBBED_SURFACE for reasons) ----------------
    applyConnectionConfig: async () => {
      throw new Error('The connection is managed by the server on web.')
    },
    saveConnectionConfig: async () => {
      throw new Error('The connection is managed by the server on web.')
    },
    testConnectionConfig: async () => ({
      baseUrl: `${window.location.origin}${basePath()}`,
      ok: true,
      version: null
    }),
    probeConnectionConfig: async remoteUrl => ({
      authMode: 'unknown',
      baseUrl: remoteUrl,
      error: 'Connection probing is not available on web.',
      providers: [],
      reachable: false,
      version: null
    }),
    oauthLoginConnectionConfig: async remoteUrl => ({
      baseUrl: remoteUrl,
      connected: false,
      ok: false
    }),
    oauthLogoutConnectionConfig: async () => ({ connected: false, ok: false }),
    cloud: {
      agentSignIn: async dashboardUrl => ({ baseUrl: dashboardUrl, connected: false }),
      discover: async () => ({ agents: [], needsOrgSelection: false }),
      login: async () => ({ ok: false, portalBaseUrl: '', signedIn: false }),
      logout: async () => ({ ok: true, portalBaseUrl: '', signedIn: false }),
      status: async () => ({ portalBaseUrl: '', signedIn: false })
    },
    profile: {
      get: async () => ({ profile: null }),
      set: async () => ({ profile: null })
    },
    notify: async payload => {
      if (!('Notification' in window) || Notification.permission === 'denied') {
        return false
      }

      // Point-of-use permission: the first real notification asks. 'default'
      // (never asked) resolves here; a denial simply reports undelivered.
      if (Notification.permission !== 'granted') {
        const permission = await Notification.requestPermission()

        if (permission !== 'granted') {
          return false
        }
      }

      new Notification(payload.title ?? 'Hermes', { body: payload.body, silent: payload.silent })

      return true
    },
    requestMicrophoneAccess: async () => true,
    selectPaths: async () => [],
    normalizePreviewTarget: async () => null,
    watchPreviewFile: async () => ({ id: '', path: '' }),
    stopPreviewFileWatch: async () => false,
    fetchLinkTitle: async () => '',
    settings: {
      getDefaultProjectDir: async () => ({ defaultLabel: '', dir: null, resolvedCwd: '' }),
      pickDefaultProjectDir: async () => ({ canceled: true, dir: null }),
      setDefaultProjectDir: async dir => ({ dir })
    },
    revealLogs: async () => ({ error: 'Not available on web.', ok: false, path: '' }),
    getRecentLogs: async () => ({ lines: [], path: '' }),
    onPreviewFileChanged: unsubscribe,
    onBackendExit: unsubscribe,
    onBootProgress: unsubscribe,
    onBootstrapEvent: unsubscribe,
    resetBootstrap: async () => ({ ok: false }),
    repairBootstrap: async () => ({ ok: false }),
    cancelBootstrap: async () => ({ cancelled: false, ok: false })
  }

  return bridge
}

/** True when the renderer is running against the web bridge (no Electron). */
export function isWebHost(): boolean {
  return window.hermesDesktop?.host === 'web'
}

/** Install the web bridge when no Electron preload has claimed the window. */
export function installWebBridge(): void {
  if (window.hermesDesktop) {
    return
  }

  window.hermesDesktop = createWebBridge()
}
