"""Corrige o ownership/permissao dos secrets Core -> OmniRoute para o UID/GID
de runtime real do container `cesar-core` -- nunca hardcoded aqui, lido de
TARGET_UID/TARGET_GID.

Por que isso existe (DEC-125): os secrets do Compose (file-provider, sem
Swarm) sao bind mounts do arquivo do HOST -- os campos uid/gid/mode da
sintaxe longa de `secrets:` so tem efeito sob Docker Swarm, nunca sob
`docker compose up` puro (o unico jeito que este bundle usa). O arquivo real
em disco foi escrito por `bootstrap-omniroute-keys.js` (roda dentro do
container OmniRoute, UID do container OmniRoute, `mode 0600`) -- ilegivel
para o UID de runtime do `cesar-core` (10001:10001, ver Dockerfile do
`cesar-core`), que e um UID completamente diferente e nao relacionado.

Este script roda uma unica vez, como root (`user: "0:0"` no compose, so
neste container efemero), ANTES do `cesar-core` subir: le o conteudo de cada
secret original (root sempre pode ler qualquer arquivo), grava uma COPIA
num volume Docker interno com o UID/GID e modo corretos para o consumidor
real, e sai. O arquivo original do bootstrap nunca e alterado -- nenhum
`chown`/`chmod` acontece nele.
"""

import os
import sys


def fail(message: str) -> None:
    print(f"FIX_SECRET_PERMISSIONS_FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    try:
        uid = int(os.environ["TARGET_UID"])
        gid = int(os.environ["TARGET_GID"])
    except (KeyError, ValueError):
        fail("TARGET_UID/TARGET_GID not set or not valid integers")

    pairs_raw = os.environ.get("SECRET_COPY_PAIRS")
    if not pairs_raw:
        fail("SECRET_COPY_PAIRS not set")

    pairs: list[tuple[str, str]] = []
    for entry in pairs_raw.split(","):
        if ":" not in entry:
            fail(f"invalid pair entry: {entry}")
        src, dst = entry.split(":", 1)
        pairs.append((src, dst))

    for src, dst in pairs:
        try:
            with open(src, "rb") as handle:
                content = handle.read()
        except OSError as error:
            fail(f"cannot read source secret {src}: {error}")
        if not content:
            fail(f"source secret {src} is empty")

        os.makedirs(os.path.dirname(dst), exist_ok=True)
        # O_EXCL evitado de proposito: idempotente, a copia e sempre
        # regravada a partir da fonte real -- nunca criamos um valor, so
        # reproduzimos o que ja existe com outra permissao.
        fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o400)
        try:
            os.write(fd, content)
        finally:
            os.close(fd)
        os.chmod(dst, 0o400)
        os.chown(dst, uid, gid)
        print(
            f"OK  {os.path.basename(dst)}: copiado com uid={uid} gid={gid} mode=0400"
        )

    print("FIX_SECRET_PERMISSIONS_DONE")


if __name__ == "__main__":
    main()
