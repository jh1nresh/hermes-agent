## Problem

Users on networks where DNS resolves **external** domains to **private IP ranges** are completely blocked from using Hermes's web tools, browser, vision URL fetching, and gateway media downloads. This affects:

- **OpenWrt routers** that use `198.18.0.0/15` (IANA benchmarking range) for DNS resolution
- **Corporate proxies** / split-tunnel VPNs that resolve all domains locally
- **Tailscale/WireGuard** setups using `100.64.0.0/10` (CGNAT range)

Example from a user's environment — `nousresearch.com` resolves to a private IP:
```
$ nslookup nousresearch.com
Name:    nousresearch.com
Address: 198.18.23.183
```

Python's `ipaddress.is_private` returns `True` for `198.18.23.183` (it's in the IANA benchmarking range `198.18.0.0/15`), so Hermes's SSRF guard blocks the request with:
```
Blocked: URL targets a private or internal network address
```

This affects **all 23 call sites** that use `is_safe_url()` — web_extract, web_crawl, browser_navigate, vision_analyze URL downloads, and media downloads across all 13 gateway platform adapters (Telegram, Discord, Slack, Matrix, Feishu, etc.).

Previously, only the browser tool had an escape hatch (`browser.allow_private_urls` in config.yaml), but the other 21 call sites had no bypass mechanism at all.

## Solution

A single global toggle in `tools/url_safety.py` that all 23 call sites inherit automatically — no changes needed at individual call sites.

### Configuration (three ways, in priority order):

1. **Env var**: `HERMES_ALLOW_PRIVATE_URLS=true`
2. **Config**: `security.allow_private_urls: true` in `~/.hermes/config.yaml`
3. **Legacy**: `browser.allow_private_urls: true` (existing key, now promotes globally)

### Security guarantees preserved

When the toggle is enabled, **cloud metadata endpoints are ALWAYS blocked** regardless:

| Target | Blocked? | Why |
|--------|----------|-----|
| `metadata.google.internal` | **Always** | GCP metadata hostname |
| `metadata.goog` | **Always** | GCP metadata hostname |
| `169.254.169.254` | **Always** | AWS/GCP/Azure metadata IP |
| `fd00:ec2::254` | **Always** | AWS metadata IPv6 |
| `192.168.1.1` | Allowed with toggle | Legitimate local network |
| `198.18.23.183` | Allowed with toggle | OpenWrt proxy resolution |
| `100.64.0.1` | Allowed with toggle | CGNAT/Tailscale |

This is the correct security trade-off: the metadata endpoints are the actual SSRF attack vector (they expose cloud instance credentials), while private IPs on the user's own network are not an attack surface the user needs protection from.

## Files changed

- `tools/url_safety.py` — Core change: `_global_allow_private_urls()` with cached config read, `_ALWAYS_BLOCKED_IPS` frozenset for metadata IPs that are never allowed, `is_safe_url()` checks the toggle after blocking metadata hostnames/IPs
- `hermes_cli/config.py` — `security.allow_private_urls: false` added to `DEFAULT_CONFIG`
- `tests/tools/test_url_safety.py` — 32 new tests across 3 test classes

## Tests

- 74 url_safety tests pass (42 existing + 32 new)
- 98 browser SSRF + website policy + vision tests pass (no regressions)
- E2E verified: toggle works via env var, config.yaml security section, and browser legacy fallback; cloud metadata endpoints stay blocked with toggle on; public IPs unaffected

### New test classes:
- `TestGlobalAllowPrivateUrls` — toggle defaults, env var parsing (true/1/yes/false), config.yaml security section, browser fallback, precedence, caching
- `TestAllowPrivateUrlsIntegration` — full `is_safe_url()` integration with toggle: private IPs allowed, benchmark IPs allowed, CGNAT allowed, localhost allowed, **and** metadata hostname/IP/IPv6 always blocked, DNS failure still blocked, empty URL still blocked

## How to use

Users hitting this issue add one line to `~/.hermes/config.yaml`:

```yaml
security:
  allow_private_urls: true
```

Or set the env var `HERMES_ALLOW_PRIVATE_URLS=true` for a quick workaround.
