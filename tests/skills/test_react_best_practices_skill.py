"""Tests for the react-best-practices optional skill (ported from vercel-labs/agent-skills, MIT)."""

import re
from pathlib import Path

import pytest
import yaml

SKILL_DIR = (
    Path(__file__).resolve().parent.parent.parent
    / "optional-skills"
    / "web-development"
    / "react-best-practices"
)
SKILL_MD = SKILL_DIR / "SKILL.md"
REFERENCES_DIR = SKILL_DIR / "references"

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)


@pytest.fixture(scope="module")
def skill_text():
    assert SKILL_MD.is_file(), f"SKILL.md not found at {SKILL_MD}"
    return SKILL_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def parsed(skill_text):
    match = FRONTMATTER_RE.match(skill_text)
    assert match, "SKILL.md must start with YAML frontmatter delimited by ---"
    frontmatter = yaml.safe_load(match.group(1))
    body = match.group(2)
    return frontmatter, body


def test_frontmatter_is_valid_yaml_mapping(parsed):
    frontmatter, _ = parsed
    assert isinstance(frontmatter, dict)


def test_required_fields_present(parsed):
    frontmatter, _ = parsed
    for field in ("name", "description", "version", "author", "license", "platforms"):
        assert field in frontmatter, f"missing frontmatter field: {field}"


def test_name(parsed):
    frontmatter, _ = parsed
    assert frontmatter["name"] == "react-best-practices"


def test_description_length_and_shape(parsed):
    frontmatter, _ = parsed
    description = frontmatter["description"]
    assert isinstance(description, str)
    assert len(description) <= 60, f"description too long: {len(description)} chars"
    assert description.endswith(".")


def test_author_and_license(parsed):
    frontmatter, _ = parsed
    assert frontmatter["author"] == "Vercel (vercel-labs), Hermes Agent"
    assert frontmatter["license"] == "MIT"


def test_platforms(parsed):
    frontmatter, _ = parsed
    platforms = frontmatter["platforms"]
    assert isinstance(platforms, list)
    assert set(platforms) == {"linux", "macos", "windows"}


def test_hermes_metadata(parsed):
    frontmatter, _ = parsed
    hermes = frontmatter["metadata"]["hermes"]
    assert hermes["tags"] == ["React", "NextJS", "Performance", "Frontend"]
    assert hermes["related_skills"] == []


def test_body_non_empty(parsed):
    _, body = parsed
    assert len(body.strip()) > 500, "SKILL.md body should be substantive"
    assert "# React Best Practices" in body


def test_expected_sections_present(parsed):
    _, body = parsed
    for heading in ("## When to Use", "## Rule Categories", "## Pitfalls"):
        assert heading in body, f"missing section: {heading}"


def test_references_dir_exists():
    assert REFERENCES_DIR.is_dir()
    assert list(REFERENCES_DIR.glob("*.md")), "references/ should contain .md files"


def test_every_reference_file_mentioned_in_skill_md(skill_text):
    for ref in sorted(REFERENCES_DIR.glob("*.md")):
        rel = f"references/{ref.name}"
        assert rel in skill_text, f"{rel} exists but is not mentioned in SKILL.md"


def test_every_mentioned_reference_exists(skill_text):
    mentioned = set(re.findall(r"references/([A-Za-z0-9_-]+\.md)", skill_text))
    assert mentioned, "SKILL.md should mention at least one references/*.md file"
    for name in sorted(mentioned):
        assert (REFERENCES_DIR / name).is_file(), f"SKILL.md mentions references/{name} but it does not exist"


def test_reference_files_preserve_upstream_rule_ids():
    """Spot-check that upstream Vercel rule IDs survive in the ported references."""
    expected = {
        "async-waterfalls.md": ["async-parallel", "async-defer-await"],
        "bundle-optimization.md": ["bundle-barrel-imports", "bundle-dynamic-imports"],
        "server-side.md": ["server-cache-react", "server-auth-actions"],
        "client-data-fetching.md": ["client-swr-dedup"],
        "rerender-optimization.md": ["rerender-derived-state-no-effect", "rerender-no-inline-components"],
        "rendering-performance.md": ["rendering-content-visibility"],
        "js-performance.md": ["js-set-map-lookups"],
        "advanced-patterns.md": ["advanced-use-latest"],
    }
    for fname, rule_ids in expected.items():
        path = REFERENCES_DIR / fname
        assert path.is_file(), f"missing reference file: {fname}"
        text = path.read_text(encoding="utf-8")
        for rule_id in rule_ids:
            assert f"`{rule_id}`" in text, f"{fname} missing upstream rule id {rule_id}"
