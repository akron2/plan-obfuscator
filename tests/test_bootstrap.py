from __future__ import annotations

import subprocess

import pytest

from plan_obfuscator import bootstrap


def test_numeric_version_ignores_prerelease_suffix() -> None:
    assert bootstrap.numeric_version("2.1.4") == (2, 1, 4)
    assert bootstrap.numeric_version("30.0.0rc1") == (30, 0, 0)
    assert bootstrap.numeric_version("unknown") == ()


def test_current_runtime_dependencies_are_compatible() -> None:
    assert bootstrap.dependency_issues() == []


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
