"""docs/prompts.md must mirror every prompt file (CLAUDE.md: prompts are mirrored so they diff cleanly)."""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = sorted((ROOT / "prompts").glob("*.md"))


def test_there_are_prompts_to_check():
    assert PROMPTS


@pytest.mark.parametrize("prompt_file", PROMPTS, ids=lambda p: p.name)
def test_mirror_is_current(prompt_file):
    mirror = (ROOT / "docs" / "prompts.md").read_text(encoding="utf-8")
    name = f"prompts/{prompt_file.name}"
    match = re.search(
        rf"<!-- BEGIN {re.escape(name)} -->\n````markdown\n(.*?)\n````\n<!-- END {re.escape(name)} -->", mirror, re.S
    )
    assert match, f"docs/prompts.md has no mirror block for {name}"
    assert match.group(1) == prompt_file.read_text(encoding="utf-8").rstrip(), (
        f"{name} changed but docs/prompts.md was not updated; regenerate the block between its BEGIN/END markers"
    )
