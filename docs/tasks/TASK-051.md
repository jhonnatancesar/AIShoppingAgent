# TASK-051 — Preparar documentação operacional

Status: Concluída

## Objetivo

Entregar um runbook reproduzível para instalar, iniciar, manter, diagnosticar e
recuperar a operação básica da V1 em um único Ubuntu Server headless, sem
declarar o sistema pronto para produção.

## Escopo aprovado

- criar `docs/operations/linux-runbook.md` como ponto de entrada operacional;
- documentar preparação de Ubuntu Server, Docker Engine/Compose e Git;
- configurar dados não sensíveis e secrets por arquivo com permissões
  restritas;
- documentar build, migrations, início, parada, restart e atualização;
- registrar webhook/comandos do Telegram sem expor credenciais;
- verificar health, readiness, métricas, logs, Prometheus, Collector e Jaeger;
- documentar diagnóstico de PostgreSQL, worker, dead letters, circuit breaker,
  cota de IA e Telegram;
- referenciar rotação de secrets, limpeza de autenticação e desidentificação;
- documentar e validar backup operacional manual e restauração PostgreSQL;
- manter PostgreSQL e ferramentas operacionais privados por padrão;
- explicitar limitações e pendências operacionais da V1.

## Backup e restauração

- backup manual é uma proteção operacional, não uma estratégia completa de
  disaster recovery;
- o procedimento deve gerar backup de banco descartável, restaurá-lo em banco
  limpo e confirmar schema, integridade e acesso aos dados;
- backup não testado não é considerado validado;
- arquivos de backup contêm dados potencialmente pessoais e exigem acesso
  restrito; secrets nunca entram nos exemplos ou artefatos.

## Rollback

- rollback de código retorna a commit/tag/container anterior somente quando
  compatível com o schema atual;
- rollback de schema nunca executa downgrade Alembic destrutivo
  automaticamente;
- incompatibilidade entre aplicação anterior e schema atual interrompe o
  procedimento e exige avaliação manual;
- migrations destrutivas ou transformadoras exigem análise específica.

## Rede e HTTPS

- API, PostgreSQL, Prometheus, Jaeger, Collector e métricas do worker não são
  publicados diretamente na Internet pelo Compose;
- ferramentas administrativas devem usar loopback/rede interna e acesso por
  SSH tunnel, Tailscale ou canal equivalente;
- cloudflared permanece somente para desenvolvimento/validação;
- domínio, TLS, reverse proxy e endpoint HTTPS permanente ficam como pendência
  para implantação real posterior.

## Fora do escopo

- scheduler/orquestrador de coleta;
- testes permanentes das TASKs 052 e 053;
- release da TASK-054, CI/CD ou deploy automático;
- configuração do servidor remoto, domínio, TLS ou reverse proxy;
- Alertmanager, Grafana, Vault, disaster recovery completo ou infraestrutura
  de V2;
- mudanças nas regras funcionais do produto.

## Validação obrigatória

- pipeline completo;
- Docker Compose e PostgreSQL 18 reais em ambiente isolado;
- migrations, health, readiness, métricas e restart;
- backup de base descartável com arquivo restrito;
- restauração em banco limpo e conferência do dado sintético e da migration;
- comandos e links do runbook revisados sem exposição de secrets;
- revisão técnica e documental completa.

## Resultado

- `docs/operations/linux-runbook.md` consolidou preparação, operação, diagnóstico, backup,
  restauração e rollback seguro para um único Ubuntu Server headless;
- API, PostgreSQL, Prometheus e Jaeger passaram a bindar no loopback por
  padrão; Collector já permanecia no loopback e métricas do worker continuam
  somente na rede interna;
- PostgreSQL 18 e o stack completo foram iniciados em projeto Compose isolado,
  com todas as portas publicadas em `127.0.0.1`;
- migrations chegaram a `20260808_0009`; health, readiness, Prometheus, Jaeger,
  Collector e restart de API/worker foram aprovados;
- o backup descartável ficou com modo `0600` e 82.179 bytes, foi restaurado em
  banco limpo e preservou migration, contagem e registro sintético;
- containers, rede e volumes temporários foram removidos sem tocar nos dados
  normais do projeto;
- pipeline aprovado com 602 testes, 90,61% de cobertura, Ruff, Alembic,
  Gitleaks e Docker Compose.

Próxima tarefa executável: TASK-052.

