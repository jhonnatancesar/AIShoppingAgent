"""Instala a versão fixa e verificada do Gitleaks em .tools/."""

from __future__ import annotations

import hashlib
import hmac
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

VERSION = "8.29.1"
RELEASE_BASE = f"https://github.com/gitleaks/gitleaks/releases/download/v{VERSION}"
ARTIFACTS = {
    ("Windows", "AMD64"): (
        f"gitleaks_{VERSION}_windows_x64.zip",
        "e4b7d556f0cddbe23d10d8fac2ab0f29f68f019091c6599ffbeaa8a4fb71ac78",
        "gitleaks.exe",
    ),
    ("Linux", "x86_64"): (
        f"gitleaks_{VERSION}_linux_x64.tar.gz",
        "e4eb209d04e20339d77122a3bdf9cd41351255cfb27ebcb75e85325e04f88924",
        "gitleaks",
    ),
}

PROJECT_ROOT = Path(__file__).resolve().parents[1]
INSTALL_DIRECTORY = PROJECT_ROOT / ".tools" / "gitleaks" / VERSION


def executable_path() -> Path:
    """Retorna o caminho reproduzível do executável para a plataforma atual."""
    artifact = ARTIFACTS.get((platform.system(), platform.machine()))
    if artifact is None:
        raise SystemExit(
            f"Unsupported Gitleaks platform: {platform.system()}/{platform.machine()}"
        )
    return INSTALL_DIRECTORY / artifact[2]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validated_existing(executable: Path) -> bool:
    if not executable.is_file():
        return False
    result = subprocess.run(
        [str(executable), "version"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0 and VERSION in result.stdout


def install() -> Path:
    """Baixa por HTTPS, confere SHA-256 fixado e instala sem usar latest."""
    artifact = ARTIFACTS.get((platform.system(), platform.machine()))
    if artifact is None:
        raise SystemExit(
            f"Unsupported Gitleaks platform: {platform.system()}/{platform.machine()}"
        )
    archive_name, expected_sha256, executable_name = artifact
    executable = INSTALL_DIRECTORY / executable_name
    if _validated_existing(executable):
        print(f"Gitleaks {VERSION} already verified.")
        return executable

    with tempfile.TemporaryDirectory(prefix="aishopping-gitleaks-") as temporary:
        temporary_path = Path(temporary)
        archive = temporary_path / archive_name
        urllib.request.urlretrieve(f"{RELEASE_BASE}/{archive_name}", archive)
        actual_sha256 = _sha256(archive)
        if not hmac.compare_digest(actual_sha256, expected_sha256):
            raise SystemExit("Gitleaks archive checksum verification failed")

        extracted = temporary_path / "extracted"
        extracted.mkdir()
        if archive.suffix == ".zip":
            with zipfile.ZipFile(archive) as bundle:
                bundle.extractall(extracted)
        else:
            with tarfile.open(archive, "r:gz") as bundle:
                bundle.extractall(extracted, filter="data")

        source = extracted / executable_name
        if not source.is_file():
            raise SystemExit(
                "Verified Gitleaks archive does not contain the executable"
            )
        INSTALL_DIRECTORY.mkdir(parents=True, exist_ok=True)
        destination = INSTALL_DIRECTORY / executable_name
        shutil.copyfile(source, destination)
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR)

    if not _validated_existing(executable):
        raise SystemExit("Installed Gitleaks executable failed version validation")
    print(f"Gitleaks {VERSION} installed with verified SHA-256.")
    return executable


if __name__ == "__main__":
    try:
        install()
    except (OSError, urllib.error.URLError) as error:
        print(f"Gitleaks installation failed: {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from error
