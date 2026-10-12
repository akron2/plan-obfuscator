from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from plan_obfuscator import bootstrap


def test_numeric_version_ignores_prerelease_suffix() -> None:
    assert bootstrap.numeric_version("2.1.4") == (2, 1, 4)
    assert bootstrap.numeric_version("30.0.0rc1") == (30, 0, 0)
    assert bootstrap.numeric_version("unknown") == ()


def test_current_runtime_dependencies_are_compatible() -> None:
    assert bootstrap.dependency_issues() == []


def test_project_venv_python_uses_windows_layout(tmp_path: Path) -> None:
    assert bootstrap.project_venv_python(tmp_path, "nt") == (
        tmp_path / ".venv" / "Scripts" / "python.exe"
    )


def test_current_project_venv_does_not_relaunch(tmp_path: Path) -> None:
    venv_python = bootstrap.project_venv_python(tmp_path, "nt")
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    calls: list[list[str]] = []

    result = bootstrap.run_in_project_venv(
        ["--port", "9000"],
        project_root=tmp_path,
        python_executable=venv_python,
        platform_name="nt",
        runner=lambda command, **_kwargs: calls.append(command),
    )
    assert result is None
    assert calls == []


def test_missing_project_venv_is_created_and_relaunched(tmp_path: Path) -> None:
    base_python = tmp_path / "base-python.exe"
    venv_python = bootstrap.project_venv_python(tmp_path, "nt")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:3] == ["-m", "venv"]:
            venv_python.parent.mkdir(parents=True)
            venv_python.touch()
        return subprocess.CompletedProcess(command, 0)

    result = bootstrap.run_in_project_venv(
        ["--proxy", "http://proxy:3128", "--no-browser"],
        project_root=tmp_path,
        python_executable=base_python,
        platform_name="nt",
        runner=runner,
    )
    assert result == 0
    assert calls[0][0] == [str(base_python), "-m", "venv", str(tmp_path / ".venv")]
    assert calls[1][0] == [
        str(venv_python),
        "-m",
        "plan_obfuscator.bootstrap",
        "--proxy",
        "http://proxy:3128",
        "--no-browser",
    ]


def test_extract_proxy_supports_both_cli_forms() -> None:
    assert bootstrap.extract_proxy(["--proxy", "http://proxy:3128", "--port", "9000"]) == (
        "http://proxy:3128"
    )
    assert bootstrap.extract_proxy(["--proxy=http://proxy:8080"]) == "http://proxy:8080"
    assert bootstrap.extract_proxy(["--port", "9000"]) is None


def test_extract_proxy_rejects_missing_value() -> None:
    with pytest.raises(ValueError, match="requires a URL"):
        bootstrap.extract_proxy(["--proxy"])


def test_ensure_dependencies_does_not_invoke_pip_when_satisfied() -> None:
    calls: list[list[str]] = []

    def unexpected_runner(command, **_kwargs):
        calls.append(command)
        raise AssertionError("pip must not run")

    installed = bootstrap.ensure_dependencies(
        "http://unused-proxy:3128",
        issue_reader=lambda: [],
        runner=unexpected_runner,
    )
    assert installed is False
    assert calls == []


def test_ensure_dependencies_passes_proxy_only_when_installing() -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    checks = iter([["fastapi: not installed"], []])
    installed = bootstrap.ensure_dependencies(
        "http://proxy:3128",
        issue_reader=lambda: next(checks),
        runner=runner,
    )
    assert installed is True
    assert calls[0][0][-2:] == ["--proxy", "http://proxy:3128"]
    assert calls[0][1]["check"] is True


def test_main_handles_keyboard_interrupt_without_traceback(monkeypatch) -> None:
    def interrupted(_arguments):
        raise KeyboardInterrupt

    monkeypatch.setattr(bootstrap, "run_in_project_venv", interrupted)
    assert bootstrap.main() == 130
