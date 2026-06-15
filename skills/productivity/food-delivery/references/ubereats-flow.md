# Uber Eats — browser flow

No consumer ordering API exists (the Consumer Delivery API is early-access,
NDA + written Uber approval only). Drive the consumer site at
`https://www.ubereats.com` with the `browser` tools.

## URLs

| Purpose | URL |
|---|---|
| Home / search | `https://www.ubereats.com` |
| Order history & tracking | `https://www.ubereats.com/orders` |
| Login | `https://auth.uber.com/login` (reached via the Sign in button) |

## On-page landmarks

Snapshot and match on visible text/roles rather than memorized refs — Uber
ships frequent layout and A/B changes.

- **Logged-out:** a "Sign in" / "Log in" link in the header. Hand login to the
  user (phone + OTP, or passkey).
- **Address:** a delivery-address button in the header (shows the current
  address). Click it to search and select the intended address before browsing.
- **Restaurant search:** the top search field ("Food, groceries, drinks, etc.").
- **Item add:** clicking a menu item opens a modal — set required options, then
  "Add to cart" (or "Add 1 to order").
- **Cart:** the cart button (top-right). "Go to checkout" advances to the
  checkout page.
- **Checkout:** review address, delivery time, **fees + tip**, then the final
  "Place order" button.
- **Reorder:** order history (`/orders`) exposes a "Reorder" control per past
  order — fastest path for "my usual".

## Flow

1. `browser_navigate` → home; `browser_snapshot`.
2. Verify login; set the delivery address.
3. Search the restaurant → open it → add items, handling modifier modals
   (`browser_vision` if the modal is image-heavy or ambiguous).
4. Open the cart → "Go to checkout".
5. Read back items + subtotal + delivery fee + service fee + tax + tip + ETA.
6. On explicit user confirmation, click "Place order".
7. `browser_navigate` → `/orders`, capture confirmation number + ETA.

## Notes

- Surge/"busy area" fees appear at checkout — surface them before confirming.
- Scheduled vs. ASAP delivery is chosen at checkout; default to ASAP unless the
  user asked to schedule.
- Tip defaults to a preselected percentage; confirm or adjust per the user.
