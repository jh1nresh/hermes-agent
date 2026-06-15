# Instacart — browser flow

The self-serve Developer Platform API does product search and **checkout
links** but cannot place or pay for an order programmatically (full
cart→pay→fulfillment is the partner-gated Connect API). So for actual
ordering, drive `https://www.instacart.com` with the `browser` tools. For a
structured "Shop with Instacart" link from a known item list, the optional API
path in `references/instacart-api.md` is faster — but it still hands final
checkout back to the user.

## URLs

| Purpose | URL |
|---|---|
| Store picker / browse | `https://www.instacart.com/store` |
| Order history & tracking | `https://www.instacart.com/store/account/orders` |
| Login | reached via the "Log in" button on the home page |

## On-page landmarks

- **Logged-out:** "Log in" in the header. Hand login to the user (email + OTP,
  Google/Apple SSO, or passkey).
- **Store/retailer:** Instacart is multi-retailer — pick the store first
  (Costco, Safeway, etc.); availability and price are per-store.
- **Address/ZIP:** drives which stores and delivery windows are available; set
  it before building the cart.
- **Search:** per-store search field; add items via the "Add" / "+" control on
  each product card (`browser_vision` helps read dense product grids).
- **Cart:** cart button (top-right) → "Go to checkout".
- **Checkout:** delivery window, **service fee + delivery fee + tax + tip**,
  and the substitution preference, then "Place order".
- **Reorder:** "Buy it again" / order history surfaces past items.

## Flow

1. `browser_navigate` → `/store`; `browser_snapshot`.
2. Verify login; set address/ZIP; pick the retailer.
3. Search and add each item; set quantities and substitution preference.
4. Open the cart → "Go to checkout".
5. Read back items + subtotal + delivery + service fee + tax + tip + delivery
   window, and flag any out-of-stock / substituted items.
6. On explicit user confirmation, click "Place order".
7. `browser_navigate` → order history, capture confirmation + delivery window.

## Notes

- Substitutions are core to Instacart: confirm "best match", "specific
  replacement", or "refund" before placing.
- Instacart+ membership, minimum-basket, and busy-pricing fees show at
  checkout — surface them.
- Some items are alcohol/age-restricted and need ID at delivery; flag to user.
