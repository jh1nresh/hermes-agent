"""Thin end-to-end test for image_generate's image-to-image / edit path.

Drives the real registered ``image_generate`` handler through the real module
— real catalog, real payload construction, real local-file → data-URI
encoding — and only stubs the outbound FAL HTTP submit (so it needs no FAL key
and spends no credits). One happy-path edit and one no-edit fallback; that's
the whole feature surface.
"""

from __future__ import annotations

import json
import struct
import zlib

import pytest


@pytest.fixture
def image_tool():
    import importlib
    import tools.image_generation_tool as mod
    return importlib.reload(mod)


def _tiny_png(path) -> str:
    """Write a minimal valid 1x1 PNG so the encoder can sniff + open it."""
    def chunk(typ, data):
        return (struct.pack(">I", len(data)) + typ + data
                + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
           + chunk(b"IEND", b""))
    path.write_bytes(png)
    return str(path)


def _stub_fal(image_tool, monkeypatch, captured):
    """Stub the FAL backend so we run everything except the network call."""
    monkeypatch.setattr(image_tool, "fal_key_is_configured", lambda: True)
    monkeypatch.setattr(image_tool, "_resolve_managed_fal_gateway", lambda: None)

    class _Handler:
        def get(self):
            return {"images": [{"url": "https://out/result.png", "width": 1, "height": 1}]}

    def _submit(model, arguments=None, **kw):
        captured["model"] = model
        captured["arguments"] = arguments
        return _Handler()

    monkeypatch.setattr(image_tool, "_submit_fal_request", _submit)
    # Pin an edit-capable generate model regardless of local config.
    monkeypatch.setattr(
        image_tool, "_resolve_fal_model",
        lambda: ("fal-ai/nano-banana-pro",
                 image_tool.FAL_MODELS["fal-ai/nano-banana-pro"]),
    )


def test_image_edit_e2e(image_tool, monkeypatch, tmp_path):
    """A local image + prompt routes to the edit endpoint with the image
    encoded as a data URI, and reports success."""
    captured = {}
    _stub_fal(image_tool, monkeypatch, captured)
    ref = _tiny_png(tmp_path / "ref.png")

    out = json.loads(image_tool._handle_image_generate(
        {"prompt": "make it night", "image_urls": [ref]}
    ))

    assert out["success"] is True
    assert out["image"] == "https://out/result.png"
    # Routed to the edit endpoint, local file encoded to a data URI.
    assert captured["model"] == "fal-ai/nano-banana-pro/edit"
    assert captured["arguments"]["image_urls"][0].startswith("data:image/")
    assert captured["arguments"]["prompt"] == "make it night"


def test_text_to_image_still_works(image_tool, monkeypatch):
    """No image_urls → unchanged text-to-image on the generate endpoint."""
    captured = {}
    _stub_fal(image_tool, monkeypatch, captured)

    out = json.loads(image_tool._handle_image_generate({"prompt": "a cat"}))

    assert out["success"] is True
    assert captured["model"] == "fal-ai/nano-banana-pro"
    assert "image_urls" not in captured["arguments"]
