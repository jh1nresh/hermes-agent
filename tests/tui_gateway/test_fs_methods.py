"""Tests for the remote-browsing filesystem RPCs (fs.*) and image.attach_bytes.

These power the desktop app when it talks to a gateway on a remote host (e.g. a
VPS over tailscale): the Files sidebar and path pickers browse the gateway's
filesystem via fs.list / fs.read_text / fs.read_data_url / fs.git_root, and
locally-held images are pushed to the gateway via image.attach_bytes.
"""

from __future__ import annotations

import base64
import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# 1x1 transparent PNG.
_PNG_1x1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


@pytest.fixture()
def hermes_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(home))
    yield home


@pytest.fixture()
def server(hermes_home):
    with patch.dict(
        "sys.modules",
        {
            "hermes_cli.env_loader": MagicMock(),
            "hermes_cli.banner": MagicMock(),
        },
    ):
        mod = importlib.import_module("tui_gateway.server")
        yield mod
        mod._sessions.clear()
        mod._pending.clear()
        mod._answers.clear()
        mod._methods.clear()
        importlib.reload(mod)


def _call(server, method: str, params: dict) -> dict:
    return server.handle_request({"id": "1", "method": method, "params": params})


# ── fs.list ──────────────────────────────────────────────────────────


def test_fs_list_returns_sorted_entries(server, tmp_path):
    work = tmp_path / "proj"
    work.mkdir()
    (work / "b_file.txt").write_text("x")
    (work / "a_dir").mkdir()
    (work / "node_modules").mkdir()  # hidden by filter

    resp = _call(server, "fs.list", {"path": str(work)})
    result = resp["result"]

    assert result["path"] == str(work.resolve())
    names = [e["name"] for e in result["entries"]]
    # Directories first, then files; node_modules filtered out.
    assert names == ["a_dir", "b_file.txt"]
    assert result["entries"][0]["isDirectory"] is True
    assert result["entries"][1]["isDirectory"] is False


def test_fs_list_missing_dir_reports_error(server, tmp_path):
    resp = _call(server, "fs.list", {"path": str(tmp_path / "nope")})

    assert resp["result"]["entries"] == []
    assert resp["result"]["error"] == "ENOENT"


# ── fs.read_text ─────────────────────────────────────────────────────


def test_fs_read_text_reads_file(server, tmp_path):
    target = tmp_path / "hello.py"
    target.write_text("print('hi')\n")

    resp = _call(server, "fs.read_text", {"path": str(target)})
    result = resp["result"]

    assert result["text"] == "print('hi')\n"
    assert result["language"] == "python"
    assert result["binary"] is False
    assert result["truncated"] is False


def test_fs_read_text_missing_file_errors(server, tmp_path):
    resp = _call(server, "fs.read_text", {"path": str(tmp_path / "gone.txt")})

    assert resp["error"]["code"] == 4016


def test_fs_read_text_flags_binary(server, tmp_path):
    target = tmp_path / "blob.bin"
    target.write_bytes(b"\x00\x01\x02\x03")

    result = _call(server, "fs.read_text", {"path": str(target)})["result"]

    assert result["binary"] is True


# ── fs.read_data_url ─────────────────────────────────────────────────


def test_fs_read_data_url_encodes_file(server, tmp_path):
    target = tmp_path / "pixel.png"
    target.write_bytes(_PNG_1x1)

    result = _call(server, "fs.read_data_url", {"path": str(target)})["result"]

    assert result["dataUrl"].startswith("data:image/png;base64,")
    encoded = result["dataUrl"].split(",", 1)[1]
    assert base64.b64decode(encoded) == _PNG_1x1


def test_fs_read_data_url_rejects_oversized(server, tmp_path):
    target = tmp_path / "big.bin"
    target.write_bytes(b"x")
    # Patch the cap below the file size to exercise the guard deterministically.
    with patch.object(server, "_FS_DATA_URL_MAX_BYTES", 0):
        resp = _call(server, "fs.read_data_url", {"path": str(target)})

    assert resp["error"]["code"] == 4017


# ── fs.git_root ──────────────────────────────────────────────────────


def test_fs_git_root_walks_up(server, tmp_path):
    (tmp_path / ".git").mkdir()
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)

    result = _call(server, "fs.git_root", {"path": str(nested)})["result"]

    assert result["root"] == str(tmp_path.resolve())


def test_fs_git_root_none_when_absent(server, tmp_path):
    result = _call(server, "fs.git_root", {"path": str(tmp_path)})["result"]

    assert result["root"] is None


# ── image.attach_bytes ───────────────────────────────────────────────


@pytest.fixture()
def image_session(server):
    """A session that bypasses agent build so _sess() resolves cleanly."""
    sid = "sid-img"
    # Non-empty so _sess_nowait()'s truthiness check treats it as present.
    server._sessions[sid] = {"image_counter": 0}
    with patch.object(server, "_start_agent_build", lambda *a, **k: None), patch.object(
        server, "_wait_agent", lambda s, rid: None
    ):
        yield sid


def test_image_attach_bytes_writes_and_attaches(server, hermes_home, image_session):
    data_url = "data:image/png;base64," + base64.b64encode(_PNG_1x1).decode()

    resp = _call(
        server,
        "image.attach_bytes",
        {"session_id": image_session, "data": data_url, "filename": "shot.png"},
    )
    result = resp["result"]

    assert result["attached"] is True
    saved = Path(result["path"])
    assert saved.exists()
    assert saved.read_bytes() == _PNG_1x1
    assert saved.suffix == ".png"
    # Lands under the gateway's HERMES_HOME, not the client.
    assert str(saved).startswith(str(hermes_home / "images"))
    assert server._sessions[image_session]["attached_images"] == [str(saved)]


def test_image_attach_bytes_infers_extension_from_mime(server, image_session):
    data_url = "data:image/webp;base64," + base64.b64encode(_PNG_1x1).decode()

    result = _call(
        server,
        "image.attach_bytes",
        {"session_id": image_session, "data": data_url},
    )["result"]

    assert Path(result["path"]).suffix == ".webp"


def test_image_attach_bytes_rejects_empty(server, image_session):
    resp = _call(
        server,
        "image.attach_bytes",
        {"session_id": image_session, "data": ""},
    )

    assert resp["error"]["code"] == 4015
