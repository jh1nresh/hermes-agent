// ─── Gateway connect form ───────────────────────────────────────────────
// Shown on thin-client first run (the only onboarding path) and on the
// thick client when the user picks "Connect to a remote gateway". Reuses
// the same desktop bridge IPC (probe / save / apply / oauth-login) as
// Settings → Gateway, but wrapped in the onboarding card chrome.

import { useEffect, useRef, useState } from "react"

import type { DesktopConnectionProbeResult } from "@/global"
import { useI18n } from "@/i18n"
import { AlertCircle, ChevronLeft, Globe, Loader2, LogIn } from "@/lib/icons"
import { gatewayOauthLogin, saveGatewayConnection } from "@/store/onboarding"

import { Button } from "./ui/button"
import { ErrorIcon } from "./ui/error-state"
import { Input } from "./ui/input"

type GatewayProbeStatus = 'idle' | 'probing' | 'done' | 'error'

function useGatewayProbe(url: string) {
  const [status, setStatus] = useState<GatewayProbeStatus>('idle')
  const [probe, setProbe] = useState<DesktopConnectionProbeResult | null>(null)
  const seq = useRef(0)

  useEffect(() => {
    const trimmed = url.trim()

    if (!trimmed || !/^https?:\/\//i.test(trimmed)) {
      setStatus('idle')
      setProbe(null)

      return
    }

    const desktop = window.hermesDesktop

    if (!desktop?.probeConnectionConfig) {
      return
    }

    const current = ++seq.current
    setStatus('probing')

    const timer = setTimeout(() => {
      desktop
        .probeConnectionConfig(trimmed)
        .then(result => {
          if (seq.current !== current) {
            return
          }

          setProbe(result)
          setStatus(result.reachable ? 'done' : 'error')
        })
        .catch(() => {
          if (seq.current !== current) {
            return
          }

          setProbe(null)
          setStatus('error')
        })
    }, 500)

    return () => clearTimeout(timer)
  }, [url])

  return { status, probe }
}

export function GatewayConnectForm({ onBack }: { onBack: null | (() => void) }) {
  const { t } = useI18n()
  const g = t.onboarding.gateway

  const [url, setUrl] = useState('')
  const [token, setToken] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState<'save' | 'signin' | null>(null)

  const { status: probeStatus, probe } = useGatewayProbe(url)

  const trimmedUrl = url.trim()
  const hasUrl = Boolean(trimmedUrl) && /^https?:\/\//i.test(trimmedUrl)

  // Effective auth mode: a reachable probe wins; otherwise fall back to
  // 'token' so the token box is visible by default.
  const authMode = probeStatus === 'done' && probe && probe.authMode !== 'unknown'
    ? probe.authMode
    : 'token'

  const authResolved = probeStatus === 'done'

  const providers = probe?.providers ?? []

  const providerLabel = providers.length === 1
    ? (providers[0].displayName || providers[0].name)
    : providers.length > 1
      ? providers.map(p => p.displayName || p.name).join(' / ')
      : g.identityProvider

  const isPasswordProvider = providers.length > 0 && providers.every(p => p.supportsPassword)

  const canSubmit = hasUrl && (authMode === 'oauth' ? false : Boolean(token.trim()))

  const submit = async () => {
    if (!hasUrl || busy) {
      return
    }

    setBusy('save')
    setError(null)

    const result = await saveGatewayConnection(trimmedUrl, authMode, token.trim() || undefined)

    if (!result.ok) {
      setError(result.message ?? g.saveFailed)
      setBusy(null)
    }
    // On success, applyConnectionConfig reloads the window — no need to clear busy.
  }

  const signIn = async () => {
    if (!hasUrl || busy) {
      return
    }

    setBusy('signin')
    setError(null)

    const result = await gatewayOauthLogin(trimmedUrl)

    if (!result.ok) {
      setError(result.message ?? g.signInFailed)
      setBusy(null)
    }
    // On success, applyConnectionConfig reloads the window.
  }

  return (
    <div className="grid gap-4">
      {onBack ? (
        <Button className="-mt-1 self-start font-medium" onClick={onBack} size="xs" type="button" variant="text">
          <ChevronLeft className="size-3" />
          {g.backToProviders}
        </Button>
      ) : null}

      <div className="grid gap-2">
        <label className="text-sm font-medium text-muted-foreground" htmlFor="gw-url">
          {g.urlLabel}
        </label>
        <Input
          autoComplete="off"
          autoFocus
          className={`font-mono`}
          // className={`font-mono ${hasUrl ? (probeStatus === 'probing' ? 'border-orange' : 'border-green') : ''}`}
          id="gw-url"
          onChange={e => setUrl(e.target.value)}
          placeholder="https://gateway.example.com/hermes"
          value={url}
        />
        <p className="text-xs text-muted-foreground">{g.urlHint}</p>
      </div>

      {/* Probe status */}
      {hasUrl && probeStatus === 'probing' ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          {g.probing}
        </div>
      ) : null}

      {hasUrl && probeStatus === 'error' ? (
        <div className="flex items-start gap-2 text-sm text-muted-foreground">
          <AlertCircle className="mt-0.5 size-4 shrink-0" />
          {g.probeError}
        </div>
      ) : null}

      {/* OAuth / password gateways: show sign-in button */}
      {hasUrl && authResolved && authMode === 'oauth' ? (
        <div className="grid gap-2">
          <div className="flex items-center gap-2">
            <Button disabled={Boolean(busy)} onClick={() => void signIn()}>
              {busy === 'signin' ? <Loader2 className="animate-spin" /> : <LogIn />}
              {isPasswordProvider ? g.signIn : g.signInWith(providerLabel)}
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            {isPasswordProvider ? g.passwordHint : g.oauthHint(providerLabel)}
          </p>
        </div>
      ) : null}

      {/* Token gateways: show token input */}
      {hasUrl && authResolved && authMode === 'token' ? (
        <div className="grid gap-2">
          <label className="text-sm font-medium text-muted-foreground" htmlFor="gw-token">
            {g.tokenLabel}
          </label>
          <Input
            autoComplete="off"
            className="font-mono"
            id="gw-token"
            onChange={e => setToken(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && void submit()}
            placeholder={g.tokenPlaceholder}
            type="password"
            value={token}
          />
          <p className="text-xs text-muted-foreground">{g.tokenHint}</p>
        </div>
      ) : null}

      {/* While probing (or probe error) and no saved config, neither auth UI shows —
          show a hint instead of a blank gap. */}
      {hasUrl && !authResolved ? (
        <p className="text-xs text-muted-foreground">{g.probing}</p>
      ) : null}

      {error ? (
        <div className="flex items-start gap-2 text-sm text-destructive">
          <ErrorIcon className="mt-0.5 shrink-0" size="0.875rem" />
          {error}
        </div>
      ) : null}

      <div className="flex items-center justify-between gap-3 pt-1">
        <div />
        <Button disabled={!canSubmit || Boolean(busy)} onClick={() => void submit()}>
          {busy === 'save' ? <Loader2 className="animate-spin" /> : <Globe />}
          {busy === 'save' ? g.connecting : g.connect}
        </Button>
      </div>
    </div>
  )
}

