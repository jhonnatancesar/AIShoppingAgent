# ADR-011 — Injetar secrets da V1 por arquivos com menor privilégio

## Status

Aceita em 2026-08-08 pela TASK-048.

## Contexto

Os valores sensíveis estavam ignorados pelo Git, mas o Docker Compose ainda os
injetava como variáveis de ambiente e carregava todo o `backend/.env` na API e
no worker. Isso ampliava a exposição por inspeção do contêiner e concedia ao
worker chaves de IA e segredo do webhook que ele não utiliza.

## Decisão

Produção usa exclusivamente `*_FILE` e mounts em `/run/secrets`. Valor direto
é permitido somente em desenvolvimento; valor e arquivo simultâneos falham em
qualquer ambiente. Cada serviço recebe apenas os arquivos necessários. A
imagem PostgreSQL usa `POSTGRES_PASSWORD_FILE`, e API/worker executam como
usuário non-root.

O repositório fixa Gitleaks 8.29.1 com hashes publicados embutidos no instalador
e examina working tree, conteúdo versionado e histórico. Um canário gerado em
repositório temporário prova que a detecção está funcional.

## Consequências

- valores não aparecem em `docker inspect`, layers ou ambiente do processo;
- os arquivos continuam dependendo das permissões e da proteção do host;
- Docker Compose secrets não são apresentados como cofre criptografado;
- alterar `postgres_password` não rotaciona um volume já inicializado; o papel
  precisa ser alterado dentro do PostgreSQL;
- Vault, cloud secret manager e rotação dinâmica permanecem fora da V1.
