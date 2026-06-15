# Headless, sessions & prior art

## Verdict

Fully headless end-to-end **does not work**. Uber Eats and Instacart sit behind
PerimeterX (HUMAN) / DataDome, which fingerprint headless at the TLS, WASM
CPU-timing, and behavioral layers — JS stealth patches don't cover it. Cloud
browsers (Browserbase, Browser Use) get CAPTCHA'd; a real local browser passes.

## The pattern everyone converges on

Two phases. Only the first needs a visible browser + a human:

1. **Login (headed, once).** Human solves CAPTCHA / 2FA / passkey. The
   `browser` toolset's persistent profile (Camofox managed-persistence) keeps
   the session alive across turns — same idea as the prior-art repos' profile
   dirs.
2. **Operate (effectively headless).** With the authenticated session, the bulk
   of work — search, cart, checkout prep — needs no visible interaction.

So: drive login headed, then run cart/search unattended. Final place-order keeps
a browser fallback.

## Fast lane: replay the site's own JSON/GraphQL (optional, fragile)

The fastest projects skip the DOM entirely once logged in and replay the web
client's internal calls with the session cookie:

- Uber Eats: `addItemsToDraftOrderV2` / `removeItemsFromDraftOrderV2`, then the
  checkout/order endpoints (`matiasconcha11/uber_eats_mcp`).
- Instacart: Apollo persisted-query hashes, e.g. `UpdateCartItemsMutation`,
  driven over GET (queries) / POST (mutations) — sub-second vs 20–40s of DOM
  automation (`mdwoicke/cli-printing-press-library`).

Trade-off: these endpoints and persisted-query hashes are undocumented and
change without notice. Prefer the robust DOM flow; reach for replay only when
speed matters and you can absorb the breakage.

## Prior art

| Repo | Service | Approach |
|---|---|---|
| `amrezo/mcp-ubereats` | Uber Eats | Playwright, persisted cookies, `confirm` gate |
| `matiasconcha11/uber_eats_mcp` | Uber Eats | Web JSON API + Playwright for login/fallback |
| `markswendsen-code/mcp-instacart` | Instacart | Playwright, stealth, `confirm=true` to place |
| `@striderlabs/mcp-instacart` | Instacart | Playwright, persistent profile, MFA |
| `mdwoicke/cli-printing-press-library` | Instacart | GraphQL replay via existing Chrome session |
| `Keeeeeeeks/trenchcoat-mpp` | Multi | Local real Chrome over CDP — "only thing that works" |

Shared takeaways: persist the session, never auto-place an order, expect UI/API
drift. All of which this skill already does.

## Why this skill, not those MCP servers

The MCP servers above *are* Playwright under the hood — browser-vs-browser, not
API-vs-browser. Driving Hermes' own managed browser instead wins on every axis:
one login + one cookie store (theirs is a separate profile you'd log into
twice), no extra Node process or supply-chain surface, Camofox stealth ≥ their
bundled Chromium, and selectors we control rather than a stranger's. The one
real asymmetry isn't MCP — it's that **Instacart has an official self-serve API**
(`references/instacart-api.md`) and Uber Eats has none. So: managed browser for
both, Instacart's official API as the only non-browser shortcut. Reach for an
external MCP only to outsource selector upkeep — and then for both, never split.
