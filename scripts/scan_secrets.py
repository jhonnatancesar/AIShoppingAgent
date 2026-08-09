"""Executa Gitleaks fixado sem escrever relatórios ou exibir descobertas."""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

from install_gitleaks import VERSION, install

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKTREE_CONFIG = PROJECT_ROOT / ".gitleaks.toml"
HISTORY_CONFIG = PROJECT_ROOT / "scripts" / "gitleaks-history.toml"
FORBIDDEN_SUFFIXES = {".key", ".pem", ".p12", ".pfx"}


def _run(executable: Path, *arguments: str, cwd: Path = PROJECT_ROOT) -> int:
    result = subprocess.run(
        [
            str(executable),
            *arguments,
            "--redact=100",
            "--no-banner",
            "--no-color",
        ],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode


def _tracked_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
    )
    return [path for path in result.stdout.decode().split("\0") if path]


def _is_forbidden_tracked_path(path: str) -> bool:
    normalized = PurePosixPath(path.replace("\\", "/"))
    parts = normalized.parts
    name = normalized.name.lower()
    if any(part in {".secrets", "secrets"} for part in parts):
        return True
    if name == ".env" or (name.startswith(".env.") and not name.endswith(".example")):
        return True
    return normalized.suffix.lower() in FORBIDDEN_SUFFIXES


def _validate_no_forbidden_tracked_paths() -> None:
    forbidden = [path for path in _tracked_paths() if _is_forbidden_tracked_path(path)]
    if forbidden:
        print("Secret scan failed: forbidden secret-bearing path is tracked.")
        raise SystemExit(1)


def _validate_canary(executable: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="aishopping-gitleaks-canary-") as temp:
        repository = Path(temp)
        subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
        subprocess.run(
            ["git", "config", "user.email", "security-canary@example.invalid"],
            cwd=repository,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Security Canary"],
            cwd=repository,
            check=True,
        )
        canary = "".join(("a1B2c3D4", "e5F6g7H8", "i9J0k1L2", "m3N4o5P6", "q7R8s9T0"))
        (repository / "canary.txt").write_text(
            f'google_api_key = "{canary}"\n', encoding="utf-8"
        )
        subprocess.run(["git", "add", "canary.txt"], cwd=repository, check=True)
        subprocess.run(
            ["git", "commit", "--quiet", "-m", "test: add generated canary"],
            cwd=repository,
            check=True,
        )
        exit_code = _run(executable, "git", ".", cwd=repository)
        if exit_code != 1:
            raise SystemExit(
                "Gitleaks canary validation failed: the generated fake secret was not detected"
            )


def scan() -> None:
    executable = install()
    _validate_no_forbidden_tracked_paths()
    stages = (
        (
            "working tree",
            (
                "dir",
                ".",
                "--config",
                str(WORKTREE_CONFIG),
                "--max-archive-depth=1",
                "--max-decode-depth=1",
            ),
        ),
        (
            "versioned files",
            (
                "git",
                ".",
                "--config",
                str(HISTORY_CONFIG),
                "--log-opts=-1 HEAD",
            ),
        ),
        (
            "Git history",
            (
                "git",
                ".",
                "--config",
                str(HISTORY_CONFIG),
                "--log-opts=--all",
            ),
        ),
    )
    for name, arguments in stages:
        exit_code = _run(executable, *arguments)
        if exit_code != 0:
            print(f"Secret scan failed during {name}; findings were not printed.")
            raise SystemExit(1)
        print(f"Gitleaks {VERSION}: {name} passed.")
    _validate_canary(executable)
    print("Gitleaks generated-canary detection passed.")


if __name__ == "__main__":
    try:
        scan()
    except (OSError, subprocess.SubprocessError) as error:
        print(f"Secret scan failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from error
