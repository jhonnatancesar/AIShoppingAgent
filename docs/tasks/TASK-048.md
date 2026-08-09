# TASK-048 — Proteger segredos

Status: Concluída

## Objetivo

Proteger credenciais de infraestrutura e provedores contra exposição em Git,
ambiente de contêiner, imagem, filesystem persistente e telemetria.

## Escopo

- inventário fechado dos seis secrets da V1;
- produção exclusivamente por `*_FILE`, com conflito e vazio rejeitados;
- desenvolvimento determinístico por `.env` direto ou arquivo, nunca ambos;
- Docker Compose com `/run/secrets` e concessão mínima por serviço;
- PostgreSQL por `POSTGRES_PASSWORD_FILE`, sem alegar rotação automática;
- API e worker non-root, preservando Playwright headless/headed;
- `.gitignore`/`.dockerignore` ampliados;
- Gitleaks 8.29.1 fixado, baixado com SHA-256 verificado e executado no
  working tree, conteúdo versionado e histórico;
- criação/migração/validação local sem imprimir valores;
- runbook de operação e rotação manual no Ubuntu Server.

## Fora de escopo

Vault, cloud secret manager, rotação automática/dinâmica, criptografia
gerenciada no host, resiliência da TASK-049 e privacidade geral da TASK-050.

## Critério de aceite

Configuração fail-closed, menor privilégio e ausência de canários validados em
Python, Gitleaks e Docker real isolado. Imagem, filesystem, `docker inspect`,
logs, métricas, traces e Git não podem conter os valores. Banco novo deve
inicializar pelo arquivo; banco existente deve ser rotacionado dentro do
PostgreSQL antes de reiniciar consumidores.

## Validação executada

- Gitleaks aprovou working tree, versão atual e histórico; repositório-canário
  foi rejeitado como esperado;
- PostgreSQL 18 inicializou volume novo pelo secret file e aceitou rotação
  controlada em banco existente; senha antiga foi rejeitada e a nova aceita;
- API/worker executaram como UID 999, com 6 e 2 mounts respectivamente;
- `docker inspect`, metadata/history e rootfs da imagem não continham canários;
- canários ausentes do filesystem da aplicação, logs, métricas e Jaeger;
- Chromium headless e headed aprovados sob o usuário non-root;
- pipeline completo aprovado com 573 testes e 92,53% de cobertura.

Próxima tarefa executável: TASK-049.

