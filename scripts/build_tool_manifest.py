#!/usr/bin/env python3
"""Generate ``tools/_manifest.py`` — a static list of built-in tool modules.

At runtime, ``tools.registry.discover_builtin_tools()`` reads this manifest
instead of AST-scanning every ``tools/*.py`` file to find ones that call
``registry.register()``. Saves ~145 ms at every CLI/gateway startup.

When to run:
  - Automatically: the ``discover_builtin_tools`` fallback triggers when any
    ``tools/*.py`` file has an mtime newer than the manifest. This surfaces
    a warning in dev. Run this script and commit the regenerated manifest
    to silence the warning.
  - Manually: ``python scripts/build_tool_manifest.py``
  - CI: the build-tools GitHub workflow runs this and diff-checks on every PR.

Usage:
  python scripts/build_tool_manifest.py          # regenerate in place
  python scripts/build_tool_manifest.py --check  # exit 1 if stale (for CI)
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = REPO_ROOT / "tools"
MANIFEST_PATH = TOOLS_DIR / "_manifest.py"

# Exclusions match tools/registry.py:discover_builtin_tools — these files live
# in tools/ but are infrastructure (not self-registering modules).
SKIP_FILES = {
    "__init__.py",
    "_manifest.py",
    "registry.py",
    "mcp_tool.py",  # MCP registers dynamically at runtime, not at import
}


def _is_registry_register_call(node: ast.AST) -> bool:
    """Return True when *node* is a ``registry.register(...)`` call expression."""
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return False
    func = node.value.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "register"
        and isinstance(func.value, ast.Name)
        and func.value.id == "registry"
    )


def _module_registers_tools(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError):
        return False
    return any(_is_registry_register_call(stmt) for stmt in tree.body)


def scan_tool_modules() -> list[str]:
    """Return sorted list of ``tools.<stem>`` module names that self-register."""
    return sorted(
        f"tools.{path.stem}"
        for path in TOOLS_DIR.glob("*.py")
        if path.name not in SKIP_FILES and _module_registers_tools(path)
    )


MANIFEST_HEADER = '''\
"""Auto-generated list of built-in tool modules that call ``registry.register()``.

DO NOT EDIT MANUALLY. Regenerate with:

    python scripts/build_tool_manifest.py

This file is read at startup by ``tools.registry.discover_builtin_tools()`` to
skip the ~145 ms AST scan of every ``tools/*.py`` file. When a ``tools/*.py``
file is added, modified, or removed, the dev-mode mtime check in
``discover_builtin_tools`` will log a warning and fall back to the AST scan —
run this script to regenerate and commit.

Only covers *built-in* tools (shipped in ``tools/*.py``). Plugin tools and
MCP-registered tools use separate discovery paths and are not listed here.
"""

TOOL_MODULES: tuple[str, ...] = (
'''

MANIFEST_FOOTER = ")\n"


def render_manifest(modules: list[str]) -> str:
    lines = [MANIFEST_HEADER]
    for name in modules:
        lines.append(f"    {name!r},\n")
    lines.append(MANIFEST_FOOTER)
    return "".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if the on-disk manifest doesn't match what would be generated (for CI).",
    )
    args = parser.parse_args()

    modules = scan_tool_modules()
    new_content = render_manifest(modules)

    if args.check:
        if not MANIFEST_PATH.exists():
            print(f"{MANIFEST_PATH} is missing — run: python scripts/build_tool_manifest.py", file=sys.stderr)
            return 1
        current = MANIFEST_PATH.read_text(encoding="utf-8")
        if current != new_content:
            print(
                f"{MANIFEST_PATH} is stale — run: python scripts/build_tool_manifest.py",
                file=sys.stderr,
            )
            return 1
        print(f"OK: {MANIFEST_PATH} is up-to-date ({len(modules)} tool modules).")
        return 0

    MANIFEST_PATH.write_text(new_content, encoding="utf-8")
    print(f"Wrote {MANIFEST_PATH} ({len(modules)} tool modules).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
