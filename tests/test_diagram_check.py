"""Tests for scripts/diagram_check.py and scripts/justfile_diagram.py."""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

PYTHON = sys.executable
SCRIPTS = Path(__file__).parent.parent / "scripts"
DIAGRAM_CHECK = SCRIPTS / "diagram_check.py"
JUSTFILE_DIAGRAM = SCRIPTS / "justfile_diagram.py"


def write_file(path, content):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)
    return str(p)


def run_script(script, args):
    result = subprocess.run(
        [PYTHON, script] + args,
        capture_output=True,
        text=True,
    )
    return result


class TestDiagramCheck:
    """Tests for diagram_check.py."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_pass_known_recipes(self):
        """All just references are valid recipes."""
        summary = write_file(
            f"{self.tmpdir}/summary.txt",
            "build test deploy lint",
        )
        d2 = write_file(
            f"{self.tmpdir}/chart.d2",
            'start -> "just build" -> "just test" -> done',
        )
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", summary,
            d2,
        ])
        assert result.returncode == 0
        assert "PASS 2 references in 1 files" in result.stdout

    def test_fail_unknown_recipe(self):
        """Unknown recipe name is detected."""
        summary = write_file(
            f"{self.tmpdir}/summary.txt",
            "build test",
        )
        d2 = write_file(
            f"{self.tmpdir}/chart.d2",
            'start -> "just build"\n"just deploy" -> done',
        )
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", summary,
            d2,
        ])
        assert result.returncode == 1
        assert "unknown recipe deploy" in result.stdout
        assert ":2:" in result.stdout  # line number

    def test_transition_lacks_pass(self):
        """Procedural chart transition without 'pass:' is detected."""
        d2 = write_file(
            f"{self.tmpdir}/chart.d2",
            "# procedural-chart\nstart -> running: cmd: just build",
        )
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", f"{self.tmpdir}/empty.txt",
            d2,
        ])
        # Write empty summary first
        write_file(f"{self.tmpdir}/empty.txt", "")
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", f"{self.tmpdir}/empty.txt",
            d2,
        ])
        assert result.returncode == 1
        assert "transition lacks cmd/pass" in result.stdout

    def test_transition_valid(self):
        """Procedural chart transition with cmd and pass passes."""
        write_file(f"{self.tmpdir}/empty.txt", "")
        d2 = write_file(
            f"{self.tmpdir}/chart.d2",
            "# procedural-chart\nstart -> running: cmd: echo hello | pass: exit 0",
        )
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", f"{self.tmpdir}/empty.txt",
            d2,
        ])
        assert result.returncode == 0

    def test_chained_edge(self):
        """Chained edge with cmd/pass label passes."""
        write_file(f"{self.tmpdir}/empty.txt", "")
        d2 = write_file(
            f"{self.tmpdir}/chart.d2",
            '# procedural-chart\na -> b -> c: "cmd: x | pass: y"',
        )
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", f"{self.tmpdir}/empty.txt",
            d2,
        ])
        assert result.returncode == 0

    def test_no_edge_label_fails(self):
        """Procedural chart edge with no label at all fails."""
        write_file(f"{self.tmpdir}/empty.txt", "")
        d2 = write_file(
            f"{self.tmpdir}/chart.d2",
            "# procedural-chart\nstart -> running",
        )
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", f"{self.tmpdir}/empty.txt",
            d2,
        ])
        assert result.returncode == 1
        assert "transition lacks cmd/pass" in result.stdout

    def test_edge_label_lacks_pass(self):
        """Procedural chart edge with only cmd (no pass) fails."""
        write_file(f"{self.tmpdir}/empty.txt", "")
        d2 = write_file(
            f"{self.tmpdir}/chart.d2",
            "# procedural-chart\nstart -> running: cmd: just build",
        )
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", f"{self.tmpdir}/empty.txt",
            d2,
        ])
        assert result.returncode == 1
        assert "transition lacks cmd/pass" in result.stdout

    def test_just_reference_word_boundary(self):
        """'adjust foo' is not a just reference; 'just foo' is."""
        write_file(f"{self.tmpdir}/summary.txt", "build")
        d2 = write_file(
            f"{self.tmpdir}/chart.d2",
            'adjust foo\nstart -> "just build"',
        )
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", f"{self.tmpdir}/summary.txt",
            d2,
        ])
        # Only 'just build' is detected; 'adjust foo' is not a just reference
        assert result.returncode == 0
        assert "PASS 1 references" in result.stdout

    def test_comment_lines_ignored(self):
        """Lines starting with # are not checked for just refs."""
        write_file(f"{self.tmpdir}/empty.txt", "")
        d2 = write_file(
            f"{self.tmpdir}/chart.d2",
            "# just fake_recipe\nstart -> done",
        )
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", f"{self.tmpdir}/empty.txt",
            d2,
        ])
        assert result.returncode == 0

    def test_non_procedural_file(self):
        """File without '# procedural-chart' only gets recipe check."""
        write_file(f"{self.tmpdir}/empty.txt", "")
        d2 = write_file(
            f"{self.tmpdir}/chart.d2",
            "start -> running",
        )
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", f"{self.tmpdir}/empty.txt",
            d2,
        ])
        assert result.returncode == 0  # no transition check, no just refs

    def test_multiple_files(self):
        """Check multiple files at once."""
        write_file(f"{self.tmpdir}/summary.txt", "build test")
        d2a = write_file(
            f"{self.tmpdir}/a.d2",
            'start -> "just build"',
        )
        d2b = write_file(
            f"{self.tmpdir}/b.d2",
            'start -> "just test"',
        )
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", f"{self.tmpdir}/summary.txt",
            d2a, d2b,
        ])
        assert result.returncode == 0
        assert "PASS 2 references in 2 files" in result.stdout

    def test_missing_file(self):
        """Missing input file exits 2."""
        write_file(f"{self.tmpdir}/empty.txt", "")
        result = run_script(DIAGRAM_CHECK, [
            "--summary-file", f"{self.tmpdir}/empty.txt",
            f"{self.tmpdir}/nonexistent.d2",
        ])
        assert result.returncode == 2


@pytest.mark.skipif(shutil.which("just") is None, reason="just is not installed; the recipe-graph generator needs it (CI has none)")
class TestJustfileDiagram:
    """Tests for justfile_diagram.py."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_deterministic_output(self):
        """Running twice produces identical output."""
        justfile = write_file(
            f"{self.tmpdir}/justfile",
            "build:\n    echo build\n\ntest:\n    echo test\n",
        )
        r1 = run_script(JUSTFILE_DIAGRAM, ["--justfile", justfile])
        r2 = run_script(JUSTFILE_DIAGRAM, ["--justfile", justfile])
        assert r1.returncode == 0
        assert r2.returncode == 0
        assert r1.stdout == r2.stdout

    def test_check_no_drift(self):
        """--check passes when file matches generated output."""
        justfile = write_file(
            f"{self.tmpdir}/justfile",
            "build:\n    echo build\n",
        )
        result = run_script(JUSTFILE_DIAGRAM, ["--justfile", justfile])
        expected = result.stdout

        out_file = write_file(f"{self.tmpdir}/diagram.d2", expected)
        result = run_script(JUSTFILE_DIAGRAM, [
            "--justfile", justfile,
            "--check", out_file,
        ])
        assert result.returncode == 0
        assert "no drift" in result.stdout

    def test_check_drift_detected(self):
        """--check fails when file differs from generated output."""
        justfile = write_file(
            f"{self.tmpdir}/justfile",
            "build:\n    echo build\n",
        )
        result = run_script(JUSTFILE_DIAGRAM, ["--justfile", justfile])
        expected = result.stdout

        # Write different content
        out_file = write_file(f"{self.tmpdir}/diagram.d2", "different content\n")
        result = run_script(JUSTFILE_DIAGRAM, [
            "--justfile", justfile,
            "--check", out_file,
        ])
        assert result.returncode == 1
        assert "drift detected" in result.stdout

    def test_dependency_edge_direction(self):
        """For 'b: a' (b depends on a), edge is 'a' -> 'b' (a runs first)."""
        justfile = write_file(
            f"{self.tmpdir}/justfile",
            "a:\n    echo a\n\nb: a\n    echo b\n",
        )
        result = run_script(JUSTFILE_DIAGRAM, ["--justfile", justfile])
        assert result.returncode == 0
        assert '"a" -> "b"' in result.stdout

    def test_output_format_valid(self):
        """Output lines are direction, node, or edge; no braces."""
        justfile = write_file(
            f"{self.tmpdir}/justfile",
            "a:\n    echo a\n\nb: a\n    echo b\n",
        )
        result = run_script(JUSTFILE_DIAGRAM, ["--justfile", justfile])
        assert result.returncode == 0
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            assert "{" not in line
            assert "}" not in line
            if line == "direction: down":
                continue
            # node: "name": "label"
            if line.startswith('"') and line.endswith('"') and ": " in line:
                continue
            # edge: "a" -> "b"
            if "->" in line:
                continue
            raise AssertionError(f"unexpected line: {line}")


def test_arrow_inside_quoted_label_is_not_an_edge(tmp_path):
    """A '->' inside a quoted label is text, not syntax (regression: gateway-lifecycle.d2)."""
    from importlib import util
    spec = util.spec_from_file_location("diagram_check", "scripts/diagram_check.py")
    mod = util.module_from_spec(spec); spec.loader.exec_module(mod)
    assert mod.edge_label('a -> b: "cmd: just up | pass: prints \'GET /v1/models -> http 200\'"') == '"cmd: just up | pass: prints \'GET /v1/models -> http 200\'"'
    assert mod.edge_label('title: "no edge -> here" {shape: text}') is None
    assert mod.edge_label('a -> b') == ""
    assert mod.edge_label('a -> b -> c: "cmd: x | pass: y"') == '"cmd: x | pass: y"'
