# TASK-024 — Integrar Playwright base

Status: Concluída em 2026-08-02

## Objetivo

Integrar uma base assíncrona e isolada de Playwright/Chromium para os futuros Store
Providers.

## Escopo

- Declarar Playwright como dependência da aplicação e instalar Chromium na imagem.
- Gerenciar ciclo de vida de Playwright, navegador e contexto isolado.
- Aplicar defaults seguros de headless, timeouts, locale e downloads desabilitados.
- Validar a infraestrutura com Chromium real e conteúdo local determinístico.

Seletores, URLs e providers de fontes reais permanecem na TASK-055; normalização e
persistência permanecem nas TASKs 025 e 026.

## Critério de aceite

- Chromium inicia, renderiza uma página local e encerra sem deixar sessão reutilizável.
- Inicialização duplicada e uso fora do contexto são rejeitados.
- Recursos são encerrados também após falha parcial.
- Dependência, instalação local e imagem Docker estão documentadas.

