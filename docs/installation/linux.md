# Instalação em Linux (legado)

O AIShoppingAgent rodou em produção sobre Ubuntu Server antes da migração
para o [Windows Server](windows-server.md), que é a plataforma de produção
atual. Esta página existe para quem ainda opera ou consulta uma instalação
Linux — não é o caminho recomendado para uma instalação nova.

## Documentos

- [Guia de instalação em produção — Ubuntu Server](linux-legacy-setup.md):
  manual completo, passo a passo, do zero, a partir da release `v1.0.1`.
  Escrito para Ubuntu Server e mantido como referência histórica — os
  comandos de Docker/Compose/Alembic continuam válidos em qualquer host
  Linux equivalente, mas os pontos específicos de infraestrutura do
  servidor (driver de vídeo, timer systemd) descrevem uma máquina que não
  está mais em produção.
- [Runbook operacional — Ubuntu Server](../operations/linux-runbook.md):
  rotina, backup, rollback e diagnóstico para a mesma instalação Linux.

## O que ainda se aplica

Os comandos de Docker Compose, Alembic, backup/restauração e a própria
aplicação são idênticos entre Linux e Windows — a diferença está só na
instalação do Docker, no gerenciamento de serviços do host e no mecanismo
de HTTPS público (Tailscale Funnel, hoje, em vez do `cloudflared`/reverse
proxy mencionados nesses documentos como pendentes). Para qualquer
procedimento de operação contínua (backup, rollback, diagnóstico) que não
seja específico de sistema operacional, prefira o
[Runbook de operação](../operations/runbook.md) atual, escrito para a
plataforma de produção vigente.

## O que é específico da máquina antiga

As seções sobre o driver de vídeo `nouveau`, o timer `systemd` original do
healthcheck do Tailscale Funnel e os pré-requisitos de pacote `apt`
descrevem hardware e sistema operacional que não fazem parte da produção
atual. Não siga esses pontos ao preparar um novo servidor — use
[Windows Server](windows-server.md).
