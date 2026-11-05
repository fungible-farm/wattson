"""Fail-closed pre-pack check: nothing forbidden is ever packed into a shareable artifact."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "kit_pack_check.py"
PY = sys.executable


def run_check(target: Path, allow: Path | None = None) -> subprocess.CompletedProcess:
    cmd = [PY, str(SCRIPT), str(target)]
    if allow is not None:
        cmd += ["--allow-file", str(allow)]
    return subprocess.run(cmd, capture_output=True, text=True)


def write_allow(target: Path, patterns: list[str]) -> Path:
    f = target / "allow.txt"
    f.write_text("\n".join(patterns) + "\n")
    return f


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Build a representative directory tree for pack-check tests."""
    # Allowed files
    (tmp_path / "README.md").write_text("docs")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("code")
    (tmp_path / "src" / "utils.py").write_text("code")
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "weights.gguf").write_text("model")
    (tmp_path / "model" / "config.safetensors").write_text("model")

    # Denied: CSIRO .m under data/
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "snemSA.m").write_text("case")
    (tmp_path / "data" / "sub").mkdir()
    (tmp_path / "data" / "sub" / "more.m").write_text("case")

    # Denied: workbooks
    (tmp_path / "budget.xlsx").write_text("xls")
    (tmp_path / "notes.xlsm").write_text("xlsm")
    (tmp_path / "old.xls").write_text("xls")

    # Denied: env files
    (tmp_path / ".env").write_text("secret")
    (tmp_path / ".env.local").write_text("secret")

    # Denied: keys/certs
    (tmp_path / "key.pem").write_text("pem")
    (tmp_path / "key.key").write_text("key")
    (tmp_path / "id_rsa").write_text("rsa")
    (tmp_path / "id_ed25519").write_text("ed")
    (tmp_path / "cert.p12").write_text("p12")
    (tmp_path / "bundle.pfx").write_text("pfx")

    # Denied: credential/secret in name
    (tmp_path / "aws_credentials.txt").write_text("cred")
    (tmp_path / "my_secret.txt").write_text("sec")

    # .git should be skipped
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("git")

    # Symlink to a denied file (should not be followed)
    (tmp_path / "link_to_xlsx").symlink_to(tmp_path / "budget.xlsx")

    return tmp_path


def test_clean_allowed_tree_passes(tmp_path: Path) -> None:
    """A tree with only allowed files passes with the right allow file."""
    (tmp_path / "README.md").write_text("docs")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("code")
    (tmp_path / "model").mkdir()
    (tmp_path / "model" / "weights.gguf").write_text("model")
    allow = write_allow(tmp_path, ["README.md", "src/*.py", "model/*"])
    result = run_check(tmp_path, allow)
    assert result.returncode == 0
    assert "PASS files=3" in result.stdout


def test_csiro_m_files_denied(tmp_path: Path, tree: Path) -> None:
    """*.m under data/ is always denied."""
    allow = write_allow(tree, ["*"])
    result = run_check(tree, allow)
    assert result.returncode == 1
    assert "DENY data/snemSA.m: CSIRO .m file under data/" in result.stdout
    assert "DENY data/sub/more.m: CSIRO .m file under data/" in result.stdout


def test_workbooks_denied(tmp_path: Path, tree: Path) -> None:
    """Excel workbooks are always denied."""
    allow = write_allow(tree, ["*"])
    result = run_check(tree, allow)
    assert "DENY budget.xlsx: private workbook" in result.stdout
    assert "DENY notes.xlsm: private workbook" in result.stdout
    assert "DENY old.xls: private workbook" in result.stdout


def test_env_files_denied(tmp_path: Path, tree: Path) -> None:
    """Env files are always denied."""
    allow = write_allow(tree, ["*"])
    result = run_check(tree, allow)
    assert "DENY .env: env file" in result.stdout
    assert "DENY .env.local: env file" in result.stdout


def test_key_cert_files_denied(tmp_path: Path, tree: Path) -> None:
    """Keys and certs are always denied."""
    allow = write_allow(tree, ["*"])
    result = run_check(tree, allow)
    assert "DENY key.pem: key/cert file" in result.stdout
    assert "DENY key.key: key/cert file" in result.stdout
    assert "DENY id_rsa: key/cert file" in result.stdout
    assert "DENY id_ed25519: key/cert file" in result.stdout
    assert "DENY cert.p12: key/cert file" in result.stdout
    assert "DENY bundle.pfx: key/cert file" in result.stdout


def test_credential_secret_names_denied(tmp_path: Path, tree: Path) -> None:
    """Files with credential/secret in the name are denied."""
    allow = write_allow(tree, ["*"])
    result = run_check(tree, allow)
    assert "DENY aws_credentials.txt: credential/secret in name" in result.stdout
    assert "DENY my_secret.txt: credential/secret in name" in result.stdout


def test_model_files_not_denied(tmp_path: Path, tree: Path) -> None:
    """GGUF and safetensors are model files, not denied."""
    allow = write_allow(tree, ["model/*"])
    result = run_check(tree, allow)
    assert "DENY model/weights.gguf" not in result.stdout
    assert "DENY model/config.safetensors" not in result.stdout


def test_allow_glob_star_still_denies(tmp_path: Path, tree: Path) -> None:
    """Even allow glob '*' cannot rescue denied files."""
    allow = write_allow(tree, ["*"])
    result = run_check(tree, allow)
    assert result.returncode == 1
    # data/snemSA.m is still denied
    assert "DENY data/snemSA.m: CSIRO .m file under data/" in result.stdout
    # budget.xlsx is still denied
    assert "DENY budget.xlsx: private workbook" in result.stdout


def test_unlisted_file_fails(tmp_path: Path) -> None:
    """Files not matching any allow pattern are UNLISTED."""
    (tmp_path / "allowed.txt").write_text("ok")
    (tmp_path / "not_allowed.txt").write_text("no")
    allow = write_allow(tmp_path, ["allowed.txt"])
    result = run_check(tmp_path, allow)
    assert result.returncode == 1
    assert "UNLISTED not_allowed.txt" in result.stdout
    assert "PASS" not in result.stdout


def test_no_allow_file_means_everything_unlisted(tmp_path: Path) -> None:
    """Without an allow file, all files are UNLISTED (fail closed)."""
    (tmp_path / "file.txt").write_text("content")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "code.py").write_text("code")
    result = run_check(tmp_path)
    assert result.returncode == 1
    assert "UNLISTED file.txt" in result.stdout
    assert "UNLISTED src/code.py" in result.stdout


def test_allow_file_comments_and_blanks(tmp_path: Path) -> None:
    """Comments and blank lines in allow file are ignored."""
    (tmp_path / "file.txt").write_text("content")
    allow = write_allow(tmp_path, ["# comment", "", "file.txt", "  ", "# another"])
    result = run_check(tmp_path, allow)
    assert result.returncode == 0
    assert "PASS files=1" in result.stdout


def test_trailing_slash_directory_prefix(tmp_path: Path) -> None:
    """Trailing slash in allow pattern means directory prefix."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.py").write_text("code")
    (tmp_path / "src" / "utils.py").write_text("code")
    (tmp_path / "other.txt").write_text("other")
    allow = write_allow(tmp_path, ["src/"])
    result = run_check(tmp_path, allow)
    assert "PASS" not in result.stdout
    assert "UNLISTED other.txt" in result.stdout
    # src files are allowed
    assert "UNLISTED src/main.py" not in result.stdout
    assert "UNLISTED src/utils.py" not in result.stdout


def test_missing_dir_exits_2(tmp_path: Path) -> None:
    """Missing directory argument exits with code 2."""
    result = run_check(tmp_path / "nonexistent_dir")
    assert result.returncode == 2
    assert "ERROR" in result.stdout


def test_symlink_not_followed(tmp_path: Path) -> None:
    """Symlinks are never followed and never allowed: reported as DENY by their own name, even when the name matches an allow glob."""
    (tmp_path / "real.txt").write_text("content")
    (tmp_path / "link.txt").symlink_to(tmp_path / "real.txt")
    allow = write_allow(tmp_path, ["real.txt", "link.txt"])
    result = run_check(tmp_path, allow)
    # link.txt is denied even though the allow file names it (not followed to real.txt)
    assert "DENY link.txt: symlink" in result.stdout
    # real.txt is allowed
    assert "UNLISTED real.txt" not in result.stdout


def test_git_directory_skipped(tmp_path: Path) -> None:
    """.git directory is skipped entirely."""
    (tmp_path / "file.txt").write_text("content")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("git config")
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main")
    allow = write_allow(tmp_path, ["file.txt"])
    result = run_check(tmp_path, allow)
    assert result.returncode == 0
    assert "PASS files=1" in result.stdout
    assert ".git" not in result.stdout


def test_deny_reasons_cannot_be_rescued_by_allow(tmp_path: Path) -> None:
    """Specific deny reasons cannot be overridden by allow patterns."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "case.m").write_text("m file")
    (tmp_path / "workbook.xlsx").write_text("xls")
    (tmp_path / "id_rsa").write_text("key")
    (tmp_path / "credentials.json").write_text("cred")
    allow = write_allow(tmp_path, ["data/case.m", "workbook.xlsx", "id_rsa", "credentials.json"])
    result = run_check(tmp_path, allow)
    assert result.returncode == 1
    assert "DENY data/case.m: CSIRO .m file under data/" in result.stdout
    assert "DENY workbook.xlsx: private workbook" in result.stdout
    assert "DENY id_rsa: key/cert file" in result.stdout
    assert "DENY credentials.json: credential/secret in name" in result.stdout


def test_summary_format_fail(tmp_path: Path) -> None:
    """FAIL summary includes counts."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "case.m").write_text("m")
    (tmp_path / "unlisted.txt").write_text("text")
    allow = write_allow(tmp_path, [])
    result = run_check(tmp_path, allow)
    assert result.returncode == 1
    assert "FAIL denied=1 unlisted=1 files=2" in result.stdout


def test_summary_format_pass(tmp_path: Path) -> None:
    """PASS summary includes file count."""
    (tmp_path / "file.txt").write_text("content")
    allow = write_allow(tmp_path, ["file.txt"])
    result = run_check(tmp_path, allow)
    assert result.returncode == 0
    assert "PASS files=1" in result.stdout


def test_symlinked_directory_is_denied_not_ignored(tmp_path):
    """A symlink to a directory must be reported (os.walk never descends into it, so it was invisible)."""
    import os, subprocess, sys
    tree = tmp_path / "tree"; tree.mkdir()
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "x.txt").write_text("x")
    os.symlink(outside, tree / "models")
    allow = tmp_path / "allow.txt"; allow.write_text("*\n")
    script = Path(__file__).resolve().parents[1] / "scripts" / "kit_pack_check.py"
    out = subprocess.run([sys.executable, str(script), str(tree), "--allow-file", str(allow)], capture_output=True, text=True)
    assert out.returncode == 1, out.stdout
    assert "DENY models: symlink" in out.stdout, out.stdout


def test_symlink_with_an_allowed_name_is_denied(tmp_path):
    """model.bin -> a secret elsewhere must not pass just because its own name is allowed."""
    import os, subprocess, sys
    tree = tmp_path / "tree"; tree.mkdir()
    secret = tmp_path / "id_rsa"; secret.write_text("k")
    os.symlink(secret, tree / "model.bin")
    allow = tmp_path / "allow.txt"; allow.write_text("*\n")
    script = Path(__file__).resolve().parents[1] / "scripts" / "kit_pack_check.py"
    out = subprocess.run([sys.executable, str(script), str(tree), "--allow-file", str(allow)], capture_output=True, text=True)
    assert out.returncode == 1 and "DENY model.bin: symlink" in out.stdout, out.stdout


def test_data_directory_match_is_case_insensitive(tmp_path):
    import subprocess, sys
    tree = tmp_path / "tree"; (tree / "Data").mkdir(parents=True)
    (tree / "Data" / "snemSA.M").write_text("case")
    allow = tmp_path / "allow.txt"; allow.write_text("*\n")
    script = Path(__file__).resolve().parents[1] / "scripts" / "kit_pack_check.py"
    out = subprocess.run([sys.executable, str(script), str(tree), "--allow-file", str(allow)], capture_output=True, text=True)
    assert out.returncode == 1 and "DENY Data/snemSA.M" in out.stdout, out.stdout


def test_tool_credential_files_are_denied(tmp_path):
    import subprocess, sys
    tree = tmp_path / "tree"; tree.mkdir()
    for name in (".netrc", ".npmrc", ".pypirc", "store.jks", "id_ecdsa"):
        (tree / name).write_text("x")
    allow = tmp_path / "allow.txt"; allow.write_text("*\n")
    script = Path(__file__).resolve().parents[1] / "scripts" / "kit_pack_check.py"
    out = subprocess.run([sys.executable, str(script), str(tree), "--allow-file", str(allow)], capture_output=True, text=True)
    assert out.returncode == 1 and "denied=5" in out.stdout, out.stdout
