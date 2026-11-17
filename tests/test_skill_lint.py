"""CLI tests for scripts/skill_lint.py using throwaway skill trees."""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "skill_lint.py"


def make_skill(root, dirname, *, name=None, description="Does a thing.", body=""):
    """Write root/skills/<dirname>/SKILL.md with the given frontmatter fields."""
    skill = root / "skills" / dirname
    skill.mkdir(parents=True)
    fields = []
    if name is not None:
        fields.append(f"name: {name}")
    if description is not None:
        fields.append(f"description: {description}")
    text = "---\n" + "\n".join(fields) + "\n---\n\n# Heading\n\n" + body
    (skill / "SKILL.md").write_text(text, encoding="utf-8")
    return skill


def lint(*skills):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *(str(s) for s in skills)],
        capture_output=True,
        text=True,
    )


def test_good_skill_passes(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "x.py").write_text("pass\n", encoding="utf-8")
    skill = make_skill(tmp_path, "alpha", name="alpha", body="See [x](../../shared/x.py).\n")
    result = lint(skill)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "PASS skill-lint alpha"


def test_name_must_equal_directory(tmp_path):
    result = lint(make_skill(tmp_path, "beta", name="other-name"))
    assert result.returncode == 1
    assert "FAIL skill-lint beta" in result.stdout
    assert "other-name" in result.stdout


def test_link_to_missing_file_fails(tmp_path):
    skill = make_skill(tmp_path, "gamma", name="gamma", body="[gone](../../shared/y.py)\n")
    result = lint(skill)
    assert result.returncode == 1
    assert "FAIL skill-lint gamma" in result.stdout
    assert "shared/y.py" in result.stdout


def test_missing_description_fails(tmp_path):
    result = lint(make_skill(tmp_path, "delta", name="delta", description=None))
    assert result.returncode == 1
    assert "FAIL skill-lint delta" in result.stdout
    assert "description" in result.stdout


def test_unreadable_path_exits_two(tmp_path):
    result = lint(tmp_path / "absent")
    assert result.returncode == 2
    assert "unreadable" in result.stdout


TOOL_TABLE = "## The allow-list\n\n| Need | Tool |\n|---|---|\n| a | `alpha_tool` | x |\n| b | `beta_tool` | y |\n"


def lint_with_tools(tmp_path, body, names=("alpha_tool", "beta_tool")):
    import json
    import os

    registry = tmp_path / "tools.json"
    registry.write_text(json.dumps([{"name": n, "description": "d"} for n in names]), encoding="utf-8")
    skill = make_skill(tmp_path, "gamma", name="gamma", body=body)
    return subprocess.run([sys.executable, str(SCRIPT), str(skill)], capture_output=True, text=True,
                          env={**os.environ, "SKILL_LINT_TOOLS": str(registry)})


def test_allow_list_matching_the_registry_passes(tmp_path):
    result = lint_with_tools(tmp_path, TOOL_TABLE)
    assert result.returncode == 0, result.stdout
    assert result.stdout.strip() == "PASS skill-lint gamma"


def test_allow_list_citing_an_unknown_tool_fails(tmp_path):
    result = lint_with_tools(tmp_path, TOOL_TABLE + "| c | `ghost_tool` | z |\n")
    assert result.returncode == 1
    assert "cites unknown tool ghost_tool" in result.stdout


def test_allow_list_omitting_a_registered_tool_fails(tmp_path):
    result = lint_with_tools(tmp_path, TOOL_TABLE, names=("alpha_tool", "beta_tool", "gamma_tool"))
    assert result.returncode == 1
    assert "tools.json tool not listed in skill: gamma_tool" in result.stdout
