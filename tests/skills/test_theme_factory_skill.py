"""Tests for the theme-factory Hermes optional skill port."""
import re
from pathlib import Path

import pytest
import yaml

SKILL_DIR = (
    Path(__file__).resolve().parent
    / ".." / ".." / "optional-skills" / "creative" / "theme-factory"
).resolve()
SKILL_MD = SKILL_DIR / "SKILL.md"

EXPECTED_THEMES = [
    "arctic-frost",
    "botanical-garden",
    "desert-rose",
    "forest-canopy",
    "golden-hour",
    "midnight-galaxy",
    "modern-minimalist",
    "ocean-depths",
    "sunset-boulevard",
    "tech-innovation",
]


def _split(text: str):
    assert text.startswith("---\n"), "SKILL.md must start with YAML frontmatter"
    _, fm, body = text.split("---\n", 2)
    return yaml.safe_load(fm), body


@pytest.fixture(scope="module")
def skill():
    return _split(SKILL_MD.read_text(encoding="utf-8"))


def test_skill_md_exists():
    assert SKILL_MD.is_file()


def test_frontmatter_valid_yaml(skill):
    fm, _ = skill
    assert isinstance(fm, dict)


def test_frontmatter_fields(skill):
    fm, _ = skill
    assert fm["name"] == "theme-factory"
    assert fm["version"] == "0.1.0"
    assert fm["author"] == "Anthropic (anthropics), Hermes Agent"
    assert fm["license"] == "Apache-2.0"
    assert fm["platforms"] == ["linux", "macos", "windows"]


def test_description_constraints(skill):
    fm, _ = skill
    desc = fm["description"]
    assert isinstance(desc, str)
    assert len(desc) <= 60, f"description is {len(desc)} chars (max 60)"
    assert desc.endswith(".")


def test_metadata_hermes(skill):
    fm, _ = skill
    hermes = fm["metadata"]["hermes"]
    assert hermes["tags"] == ["Themes", "Design", "HTML", "Styling"]
    assert hermes["related_skills"] == ["claude-design", "sketch"]


def test_required_sections(skill):
    _, body = skill
    for section in [
        "## When to Use",
        "## Theme Gallery",
        "## Procedure",
        "## Generating New Themes",
        "## Pitfalls",
        "## Verification",
    ]:
        assert section in body, f"missing section: {section}"


def test_upstream_attribution(skill):
    _, body = skill
    intro = body[:600]
    assert "anthropics/skills" in intro
    assert "Apache-2.0" in intro


def test_theme_reference_files_exist():
    themes_dir = SKILL_DIR / "references" / "themes"
    assert themes_dir.is_dir()
    found = sorted(p.stem for p in themes_dir.glob("*.md"))
    assert found == EXPECTED_THEMES


def test_license_file_is_apache():
    lic = SKILL_DIR / "references" / "LICENSE.txt"
    assert lic.is_file()
    text = lic.read_text(encoding="utf-8")
    assert "Apache License" in text and "Version 2.0" in text


def test_gallery_table_matches_theme_count(skill):
    _, body = skill
    gallery = body.split("## Theme Gallery", 1)[1].split("##", 1)[0]
    rows = [
        line for line in gallery.splitlines()
        if re.match(r"^\|\s*\d+\s*\|", line)
    ]
    assert len(rows) == len(EXPECTED_THEMES) == 10


def test_gallery_names_match_reference_files(skill):
    _, body = skill
    gallery = body.split("## Theme Gallery", 1)[1].split("##", 1)[0]
    for stem in EXPECTED_THEMES:
        display = stem.replace("-", " ").title()
        assert display in gallery, f"gallery missing theme: {display}"


def test_theme_files_have_palette_and_typography():
    for stem in EXPECTED_THEMES:
        text = (SKILL_DIR / "references" / "themes" / f"{stem}.md").read_text(
            encoding="utf-8"
        )
        assert "## Color Palette" in text
        assert "## Typography" in text
        assert len(re.findall(r"#[0-9a-fA-F]{6}", text)) >= 4


def test_skill_md_references_theme_dir(skill):
    _, body = skill
    assert "references/themes/" in body


def test_hermes_tool_framing(skill):
    _, body = skill
    for tool in ["write_file", "read_file", "browser_navigate", "browser_vision"]:
        assert f"`{tool}`" in body, f"missing Hermes tool framing: {tool}"
    assert "architecture-diagram" in body
