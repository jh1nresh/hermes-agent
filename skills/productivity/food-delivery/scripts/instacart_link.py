#!/usr/bin/env python3
"""Optional power-up: build an Instacart shoppable cart link via the IDP API.

The browser flow needs no keys. This is for users who set
``INSTACART_IDP_API_KEY`` (a self-serve Instacart Developer Platform key) and
want a structured, one-tap "Shop with Instacart" link instead of driving the
cart by hand. The API never places or pays for an order — it returns a
``products_link_url`` the user opens to check out on Instacart.

Usage:
    python instacart_link.py --title "Taco night" --item "tortillas:8:count" \\
        --item "ground beef:1:pound" --item lime:6:count

Each ``--item`` is ``name[:quantity[:unit]]``. Reads the API key from
INSTACART_IDP_API_KEY (env or ~/.hermes/.env). Pass --dev to hit the
development host.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROD_BASE = "https://connect.instacart.com"
DEV_BASE = "https://connect.dev.instacart.tools"
PRODUCTS_LINK_PATH = "/idp/v1/products/products_link"

ENV_KEY = "INSTACART_IDP_API_KEY"


def api_base(dev: bool = False) -> str:
    return DEV_BASE if dev else PROD_BASE


def parse_item(spec: str) -> Dict[str, Any]:
    """``name[:quantity[:unit]]`` -> a line_items entry. Name may contain spaces."""
    parts = [p.strip() for p in spec.split(":")]
    name = parts[0]
    if not name:
        raise ValueError(f"item spec missing a name: {spec!r}")
    item: Dict[str, Any] = {"name": name}
    if len(parts) > 1 and parts[1]:
        item["quantity"] = float(parts[1]) if "." in parts[1] else int(parts[1])
    if len(parts) > 2 and parts[2]:
        item["unit"] = parts[2]
    return item


def build_products_link_request(
    title: str,
    items: List[str],
    *,
    api_key: str,
    dev: bool = False,
    instructions: Optional[List[str]] = None,
    image_url: Optional[str] = None,
) -> Tuple[str, Dict[str, str], Dict[str, Any]]:
    """Return ``(url, headers, body)`` for the products_link call. No network."""
    if not title:
        raise ValueError("title is required")
    line_items = [parse_item(s) for s in items]
    if not line_items:
        raise ValueError("at least one --item is required")
    body: Dict[str, Any] = {"title": title, "link_type": "shopping_list", "line_items": line_items}
    if instructions:
        body["instructions"] = instructions
    if image_url:
        body["image_url"] = image_url
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    return f"{api_base(dev)}{PRODUCTS_LINK_PATH}", headers, body


def resolve_api_key() -> str:
    key = os.environ.get(ENV_KEY)
    if key:
        return key
    env_path = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{ENV_KEY}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit(
        f"{ENV_KEY} not set. Use the browser flow (no key needed), or add the key "
        "to ~/.hermes/.env — see references/instacart-api.md."
    )


def create_products_link(title: str, items: List[str], *, dev: bool = False, **kw: Any) -> Dict[str, Any]:
    url, headers, body = build_products_link_request(
        title, items, api_key=resolve_api_key(), dev=dev, **kw
    )
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 - fixed Instacart host
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"Instacart API {e.code}: {e.read().decode(errors='replace')}") from e


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Build an Instacart shoppable cart link (IDP API).")
    ap.add_argument("--title", required=True)
    ap.add_argument("--item", dest="items", action="append", default=[], help="name[:quantity[:unit]]")
    ap.add_argument("--instruction", dest="instructions", action="append", default=[])
    ap.add_argument("--image-url")
    ap.add_argument("--dev", action="store_true", help="use the development host")
    args = ap.parse_args(argv)

    out = create_products_link(
        args.title,
        args.items,
        dev=args.dev,
        instructions=args.instructions or None,
        image_url=args.image_url,
    )
    print(json.dumps({"products_link_url": out.get("products_link_url"), "raw": out}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
