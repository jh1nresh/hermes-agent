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
    'tools.browser_cdp_tool',
    'tools.browser_dialog_tool',
    'tools.browser_tool',
    'tools.clarify_tool',
    'tools.code_execution_tool',
    'tools.cronjob_tools',
    'tools.delegate_tool',
    'tools.discord_tool',
    'tools.feishu_doc_tool',
    'tools.feishu_drive_tool',
    'tools.file_tools',
    'tools.homeassistant_tool',
    'tools.image_generation_tool',
    'tools.memory_tool',
    'tools.mixture_of_agents_tool',
    'tools.process_registry',
    'tools.rl_training_tool',
    'tools.send_message_tool',
    'tools.session_search_tool',
    'tools.skill_manager_tool',
    'tools.skills_tool',
    'tools.terminal_tool',
    'tools.todo_tool',
    'tools.tts_tool',
    'tools.vision_tools',
    'tools.web_tools',
    'tools.yuanbao_tools',
)
