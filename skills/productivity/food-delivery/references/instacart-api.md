# Instacart Developer Platform API — optional power-up

The browser flow needs **no key**. This path is opt-in: it turns a known item
list into a one-tap "Shop with Instacart" link. It never places or pays for an
order — the user opens the link and checks out on Instacart.

## When to prefer it

- The user gave an explicit list ("tortillas, ground beef, limes, cilantro")
  and you want a clean handoff link instead of hand-driving the cart.
- You're composing a recipe/meal into a shoppable list.

For "order my usual", live price checks, or tracking an order, use the browser
flow (`references/instacart-flow.md`).

## Setup

1. Get a self-serve key at the Instacart Developer Platform
   (`https://docs.instacart.com/developer_platform_api`).
2. Store it as a secret (it's a credential, so it belongs in `.env`):

   ```bash
   hermes setup            # add INSTACART_IDP_API_KEY when prompted
   # or append to ~/.hermes/.env:  INSTACART_IDP_API_KEY=ic_...
   ```

## Use

```bash
python scripts/instacart_link.py \
  --title "Taco night" \
  --item "tortillas:8:count" \
  --item "ground beef:1:pound" \
  --item lime:6:count \
  --instruction "Pick ripe avocados"
```

Each `--item` is `name[:quantity[:unit]]`. The script POSTs to
`/idp/v1/products/products_link` and prints a `products_link_url`. Pass `--dev`
to use the development host while testing.

## Capabilities & limits

- **Can:** product search, build a cart/shopping-list, generate a checkout
  link, list nearby retailers, recipe pages.
- **Cannot:** place an order, pay, or read someone's order status — those need
  the partner-gated Connect API. The link hands checkout to the user.
