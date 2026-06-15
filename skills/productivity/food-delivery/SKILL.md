---
name: food-delivery
description: "Order food and groceries via Uber Eats and Instacart."
version: 1.0.0
author: Brooklyn + Hermes Agent
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [food, delivery, groceries, ubereats, instacart, ordering, browser]
    category: productivity
    requires_toolsets: [browser]
---

# Food Delivery Skill

Order restaurant food (Uber Eats) and groceries (Instacart) by driving the
real consumer sites in the Hermes browser with your already-logged-in
session. There is no self-serve consumer ordering API for either service, so
the browser is the path: it sees the same menus, prices, fees, and saved
payment methods you do.

This skill builds and reviews carts and places orders **only after you
confirm**. It does not enter new payment cards or addresses — it uses what's
already saved on the account. It is not a partner/merchant integration.

## When to Use

- "order me dinner from <restaurant>", "get my usual", "reorder last night's"
- "order groceries", "add milk, eggs, and bananas to Instacart", "restock coffee"
- "what's the delivery ETA / where's my order" → check an in-progress order
- Service routing: restaurant/prepared food → **Uber Eats**; grocery/store
  items → **Instacart**. If ambiguous, ask.

## Prerequisites

- The `browser` toolset is configured and `browser_navigate` works.
- A **persistent browser profile** (Camofox managed-persistence, the default
  managed browser) so a login survives across turns and sessions. Without it
  you'll re-login every task.
- You are logged in to the service once in the Hermes browser. First run walks
  you through it; OTP/passkey/CAPTCHA steps are handed to you (see Pitfalls).
- A saved delivery address and payment method on the account. This skill will
  not type in a new card.
- Optional power-up (no key needed for the browser flow): an Instacart
  Developer Platform key in `INSTACART_IDP_API_KEY` enables a structured
  "Shop with Instacart" cart link via `scripts/instacart_link.py` — see
  `references/instacart-api.md`.

## How to Run

Drive the page with the `browser` tools. The loop is always: `browser_snapshot`
to get fresh element refs → act (`browser_click` / `browser_type` /
`browser_press`) → re-snapshot. Refs go stale after any navigation, so never
reuse a ref across steps. When the accessibility tree is ambiguous (item cards,
image-heavy menus, modifier modals), use `browser_vision` to read the rendered
page.

Per-service walkthroughs with URLs and on-page landmarks:

- Uber Eats → `references/ubereats-flow.md`
- Instacart → `references/instacart-flow.md`
- Headless reality, session reuse, prior art → `references/headless-and-sessions.md`

Login is the only step that truly needs a visible browser + the user (CAPTCHA /
2FA / passkey). Once logged in, the persistent profile keeps you authenticated,
so the rest runs unattended.

## Quick Reference

| Intent | Service | Entry URL |
|---|---|---|
| Order restaurant food | Uber Eats | `https://www.ubereats.com` |
| Track a food order | Uber Eats | `https://www.ubereats.com/orders` |
| Order / add groceries | Instacart | `https://www.instacart.com/store` |
| Track a grocery order | Instacart | `https://www.instacart.com/store/account/orders` |
| Structured cart link (opt-in API) | Instacart | `terminal`: `python scripts/instacart_link.py …` |

## Procedure

1. **Route** the request to a service (table above). Confirm the store/restaurant
   when the user named one loosely ("the taco place") by searching on-site.
2. **Open** the entry URL with `browser_navigate`, then `browser_snapshot`.
3. **Check auth.** If the snapshot shows a logged-out state (Sign in / Log in
   landmarks), go to the login page and **hand control to the user** for
   credentials, OTP, or passkey — do not guess or loop. Once logged in, the
   persistent profile keeps you in for later turns.
4. **Confirm the address.** Delivery address drives availability, prices, and
   fees. If it's not the user's intended one, set it before building the cart.
5. **Build the cart.** Search items / open the restaurant, add each item, and
   apply required modifiers (size, options, substitutions). For "my usual" or
   "reorder", use the order history / Reorder control.
6. **Review out loud.** Summarize cart contents, subtotal, **delivery fee,
   service fee, taxes, and tip**, plus the ETA. Surface Instacart item
   substitutions and any surge/busy fees explicitly.
7. **Confirm before paying.** State the final total and ask the user to confirm.
   Place the order (Place order / Checkout) **only on an explicit yes**.
8. **Verify** (below) and report the confirmation number + ETA.

## Pitfalls

- **Never place an order, change the tip, or switch payment without explicit
  user confirmation.** This spends real money.
- **Stale refs.** Any click that navigates invalidates prior refs —
  `browser_snapshot` again before the next action.
- **Auth walls.** Logged-out mid-flow, OTP, passkey, or CAPTCHA → stop and ask
  the user to complete it. Don't retry the same failing action; report the
  blocker and the next step.
- **No fully-headless end-to-end.** Both sites run PerimeterX/DataDome; headless
  and cloud browsers get blocked. Use the real managed browser, headed for
  login. See `references/headless-and-sessions.md`.
- **Address/store changes everything.** Switching the delivery address can
  re-price the cart or make items unavailable. Set it first, re-verify after.
- **Instacart substitutions.** Items get substituted or go out of stock; set or
  confirm the substitution preference and surface it in the review.
- **Fees are not the subtotal.** Always report delivery + service fees + tax +
  tip, not just item prices.

## Verification

- After placing, `browser_navigate` to the order-tracking URL and
  `browser_snapshot` (or `browser_vision`) to capture the **confirmation
  number and ETA**; report both back to the user.
- For the optional API path, a successful `scripts/instacart_link.py` run prints
  a `products_link_url` — give that link to the user to complete checkout on
  Instacart.
