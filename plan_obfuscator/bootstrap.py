from __future__ import annotations

import importlib
import importlib.metadata
import os
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class RuntimeRequirement:
    distribution: str
    minimum: tuple[int, ...]
    maximum: tuple[int, ...]


RUNTIME_REQUIREMENTS = (
    RuntimeRequirement("alembic", (1, 14), (2,)),
    RuntimeRequirement("fastapi", (0, 115), (1,)),
    RuntimeRequirement("jinja2", (3, 1), (4,)),
    RuntimeRequirement("python-multipart", (0, 0, 18), (1,)),
    RuntimeRequirement("sqlalchemy", (2, 0), (3,)),
    RuntimeRequirement("sqlglot", (26,), (31,)),
    RuntimeRequirement("uvicorn", (0, 32), (1,)),
)


def numeric_version(value: str) -> tuple[int, ...]:
    """Return the stable numeric prefix of a PEP 440-style version."""

    match = re.match(r"\s*(\d+(?:\.\d+)*)", value)
    if match is None:
        return ()
    return tuple(int(part) for part in match.group(1).split("."))


def dependency_issues(
    version_reader: Callable[[str], str] = importlib.metadata.version,
) -> list[str]:
    issues: list[str] = []
    for requirement in RUNTIME_REQUIREMENTS:
        try:
            installed_text = version_reader(requirement.distribution)
        except importlib.metadata.PackageNotFoundError:
            issues.append(f"{requirement.distribution}: not installed")
            continue
        installed = numeric_version(installed_text)
        if not installed or installed < requirement.minimum or installed >= requirement.maximum:
            minimum = ".".join(map(str, requirement.minimum))
            maximum = ".".join(map(str, requirement.maximum))
            issues.append(
                f"{requirement.distribution}: {installed_text} is outside "
                f">={minimum}, <{maximum}"
            )
    return issues


def extract_proxy(arguments: Sequence[str]) -> str | None:
    proxy: str | None = None
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--proxy":
            if index + 1 >= len(arguments) or arguments[index + 1].startswith("--"):
                raise ValueError("--proxy requires a URL")
            proxy = arguments[index + 1]
            index += 2
            continue
        if argument.startswith("--proxy="):
            proxy = argument.split("=", 1)[1]
            if not proxy:
                raise ValueError("--proxy requires a URL")
        index += 1
    return proxy


def ensure_dependencies(
    proxy: str | None,
    *,
    issue_reader: Callable[[], list[str]] = dependency_issues,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    issues = issue_reader()
    if not issues:
        print("Runtime dependencies are already installed; skipping pip.")
        return False

    print("Installing missing or incompatible runtime dependencies:")
    for issue in issues:
        print(f"  - {issue}")

    command = [sys.executable, "-m", "pip", "install", "-e", str(PROJECT_ROOT)]
    if proxy:
        command.extend(["--proxy", proxy])
    runner(command, cwd=PROJECT_ROOT, check=True, text=True)
    importlib.invalidate_caches()

    remaining = issue_reader()
    if remaining:
        raise RuntimeError(
            "Dependency installation completed, but requirements are still unmet: "
            + "; ".join(remaining)
        )
    return True


def main() -> int:
    if sys.version_info < (3, 11):  # noqa: UP036
        print("Plan Obfuscator requires Python 3.11 or newer.", file=sys.stderr)
        return 2

    try:
        proxy = extract_proxy(sys.argv[1:])
        ensure_dependencies(proxy)
    except (ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"Bootstrap failed: {error}", file=sys.stderr)
        return 1

    os.chdir(PROJECT_ROOT)
    from plan_obfuscator.cli import main as application_main

    application_main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

