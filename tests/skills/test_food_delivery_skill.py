from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

SKILL_DIR = (
    Path(__file__).resolve().parents[2]
    / "skills"
    / "productivity"
    / "food-delivery"
)
SKILL_MD = SKILL_DIR / "SKILL.md"
SCRIPT_PATH = SKILL_DIR / "scripts" / "instacart_link.py"


def load_module():
    spec = importlib.util.spec_from_file_location("food_delivery_instacart_link", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def frontmatter() -> dict[str, str]:
    text = SKILL_MD.read_text(encoding="utf-8")
    block = text.split("---", 2)[1]
    return {
        m.group(1): m.group(2).strip()
        for m in re.finditer(r"^([a-zA-Z_]+):\s*(.*)$", block, re.MULTILINE)
    }


def test_description_within_limit_and_well_formed():
    desc = frontmatter()["description"].strip('"')
    assert len(desc) <= 60, f"{len(desc)} chars: {desc!r}"
    assert desc.endswith(".")
    assert "food-delivery" not in desc.lower()


def test_required_sections_present():
    body = SKILL_MD.read_text(encoding="utf-8")
    for heading in (
        "## When to Use",
        "## Prerequisites",
        "## How to Run",
        "## Quick Reference",
        "## Procedure",
        "## Pitfalls",
        "## Verification",
    ):
        assert heading in body, f"missing section: {heading}"


def test_referenced_files_exist():
    for rel in (
        "references/ubereats-flow.md",
        "references/instacart-flow.md",
        "references/instacart-api.md",
        "references/headless-and-sessions.md",
        "scripts/instacart_link.py",
    ):
        assert (SKILL_DIR / rel).exists(), f"missing referenced file: {rel}"


def test_confirmation_gate_is_documented():
    """Spending money must require explicit user confirmation."""
    body = SKILL_MD.read_text(encoding="utf-8").lower()
    assert "confirm" in body
    assert "never place an order" in body


def test_parse_item_variants():
    mod = load_module()
    assert mod.parse_item("lime") == {"name": "lime"}
    assert mod.parse_item("ground beef:1:pound") == {"name": "ground beef", "quantity": 1, "unit": "pound"}
    assert mod.parse_item("oil:1.5:liter")["quantity"] == 1.5
    with pytest.raises(ValueError):
        mod.parse_item(":2:count")


def test_build_products_link_request_shape():
    mod = load_module()
    url, headers, body = mod.build_products_link_request(
        "Taco night",
        ["tortillas:8:count", "lime"],
        api_key="ic_test",
        instructions=["ripe avocados"],
    )
    assert url == "https://connect.instacart.com/idp/v1/products/products_link"
    assert headers["Authorization"] == "Bearer ic_test"
    assert body["title"] == "Taco night"
    assert body["link_type"] == "shopping_list"
    assert body["line_items"][0] == {"name": "tortillas", "quantity": 8, "unit": "count"}
    assert body["instructions"] == ["ripe avocados"]


def test_build_products_link_request_dev_host_and_validation():
    mod = load_module()
    url, _, _ = mod.build_products_link_request("x", ["a"], api_key="k", dev=True)
    assert url.startswith("https://connect.dev.instacart.tools")
    with pytest.raises(ValueError):
        mod.build_products_link_request("", ["a"], api_key="k")
    with pytest.raises(ValueError):
        mod.build_products_link_request("t", [], api_key="k")


def test_resolve_api_key_reads_env_file(tmp_path, monkeypatch):
    mod = load_module()
    monkeypatch.delenv("INSTACART_IDP_API_KEY", raising=False)
    home = tmp_path / ".hermes"
    home.mkdir()
    (home / ".env").write_text('INSTACART_IDP_API_KEY="ic_from_file"\n', encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(home))
    assert mod.resolve_api_key() == "ic_from_file"
