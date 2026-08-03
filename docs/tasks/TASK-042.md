# TASK-042 — Definir catálogo de eventos

Status: Concluída em 2026-08-02

## Objetivo

Definir o catálogo fechado, versionado e executável dos eventos de domínio da V1.

## Escopo

- Definir nomes estáveis, agregados e payloads tipados.
- Validar invariantes de transição, coleta, preço e disponibilidade.
- Documentar evolução de versão e limites de dados seguros.
- Não persistir, publicar, consumir, detectar ou notificar eventos.

## Ordem e dependências

Executar antes da TASK-027. O catálogo de eventos define a base para os alertas de preço.

## Critério de aceite

Catálogo imutável cobre os fatos previstos no MVP, rejeita tipos desconhecidos e
payloads incompatíveis e mantém publicação, consumo e alertas fora do escopo.
