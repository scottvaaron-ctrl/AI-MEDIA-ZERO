"""The scheduled cycle used to fail silently: Smart App Control blocked the
unsigned aimz.exe shim, and PowerShell still exited 0, so Task Scheduler
recorded success while nothing ran. These tests pin the two fixes."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from aimz import scheduler


@pytest.fixture
def project(tmp_path: Path) -> Path:
    scripts = tmp_path / ".venv" / ("Scripts" if sys.platform == "win32" else "bin")
    scripts.mkdir(parents=True)
    (scripts / ("python.exe" if sys.platform == "win32" else "python")).write_text("")
    (tmp_path / "data" / "logs").mkdir(parents=True)
    return tmp_path


def test_runner_launches_the_interpreter_not_the_shim(project: Path) -> None:
    body = scheduler.runner_script(project).read_text(encoding="utf-8")
    assert "-m aimz run" in body
    assert '"`"$py`" -m aimz run' in body
    # The shim may be named in a comment, but never on an executable line.
    code = [ln for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert not any("aimz.exe" in ln for ln in code)
    assert str(scheduler.venv_python(project)) in body


def test_runner_writes_utf8_not_utf16(project: Path) -> None:
    body = scheduler.runner_script(project).read_text(encoding="utf-8")
    # PowerShell 5.1's own `*>>` writes UTF-16 and wraps stderr in error records.
    code = [ln for ln in body.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert not any("*>>" in ln for ln in code)
    assert "cmd.exe /c" in body
    assert "$env:PYTHONUTF8 = '1'" in body


def test_runner_treats_a_failed_launch_as_failure(project: Path) -> None:
    body = scheduler.runner_script(project).read_text(encoding="utf-8")
    # Clearing the exit code first is what makes "never started" detectable.
    assert "$global:LASTEXITCODE = $null" in body
    assert "if ($null -eq $code)" in body
    assert "exit $code" in body.splitlines()[-1] or body.rstrip().endswith("exit $code")


def test_venv_python_falls_back_to_current_interpreter(tmp_path: Path) -> None:
    assert scheduler.venv_python(tmp_path) == Path(sys.executable)


def test_task_xml_survives_battery_and_sleep(project: Path) -> None:
    xml = scheduler._task_xml("09:00", project / "scripts" / "run-cycle.ps1", project)
    assert "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>" in xml
    assert "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>" in xml
    assert "<StartWhenAvailable>true</StartWhenAvailable>" in xml
    assert "<WakeToRun>true</WakeToRun>" in xml
    assert "T09:00:00</StartBoundary>" in xml


def test_cron_line_also_uses_the_interpreter(project: Path) -> None:
    line = scheduler.cron_line(project, ["09:00"])
    assert "-m aimz run" in line
    assert line.startswith("0 9 * * *")


def test_runner_starts_ollama_before_the_cycle(project: Path) -> None:
    body = scheduler.runner_script(project).read_text(encoding="utf-8")
    # Scheduled runs failed with "model unreachable" when Ollama was not open.
    assert "function Test-Ollama" in body
    assert "127.0.0.1:11434/api/tags" in body
    assert "Start-Process -FilePath $ollama.Source -ArgumentList 'serve'" in body
    # The guard must run before the cycle, and give up loudly rather than run without a model.
    assert body.index("Test-Ollama") < body.index("-m aimz run")
    assert "FATAL: ollama did not answer" in body
