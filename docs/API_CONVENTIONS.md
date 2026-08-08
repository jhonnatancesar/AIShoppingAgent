# Convenções de API

Este documento define o contrato HTTP permanente do AIShoppingAgent. Novos endpoints devem seguir estas regras e declarar seu contrato no OpenAPI gerado pelo FastAPI.

## Estrutura de rotas

- Endpoints de negócio usam o prefixo `/api/v1`.
- Endpoints operacionais, como `GET /health`, não recebem versão.
- Recursos usam substantivos plurais em minúsculas e `kebab-case` quando houver mais de uma palavra.
- Identificadores de recursos fazem parte do caminho: `/api/v1/missions/{mission_id}`.
- Filtros, ordenação e paginação usam parâmetros de consulta.
- Barras finais não fazem parte do caminho canônico.

## Métodos e códigos HTTP

- `GET` consulta recursos sem alterar estado.
- `POST` cria recursos ou executa comandos não idempotentes.
- `PUT` substitui integralmente um recurso quando essa operação existir.
- `PATCH` altera parcialmente um recurso.
- `DELETE` remove ou encerra um recurso conforme as regras do domínio.
- Criações respondem com `201 Created`; operações sem corpo respondem com `204 No Content`.
- Requisições válidas respondem com códigos `2xx`; redirecionamentos não fazem parte do contrato normal.

## Representação JSON

- Requisições e respostas usam JSON UTF-8.
- Campos usam `snake_case`, acompanhando os modelos Python.
- Datas e horários usam ISO 8601 em UTC, com deslocamento explícito, por exemplo `2026-08-01T18:30:00Z`.
- Valores monetários não usam ponto flutuante. O contrato de cada recurso deve representar valor decimal de forma exata e informar a moeda no padrão ISO 4217.
- Campos ausentes e campos com valor `null` têm significados distintos e devem ser modelados explicitamente.
- Respostas usam modelos tipados; dicionários sem contrato não devem ser expostos diretamente.

## Erros

Erros da aplicação usam um envelope estável:

```json
{
  "error": {
    "code": "stable_machine_code",
    "message": "Mensagem legível para o cliente.",
    "details": null
  }
}
```

- `code` é estável, em inglês e `snake_case`.
- `message` pode ser apresentada ao cliente e não deve revelar segredos ou detalhes internos.
- `details` é opcional e contém somente informações seguras e estruturadas.
- `400` representa requisição semanticamente inválida; `401`, ausência de autenticação; `403`, falta de permissão; `404`, recurso inexistente; `409`, conflito de estado; `422`, falha de validação estrutural; `429`, limite excedido; `500`, falha interna não exposta.

## Coleções

- Listagens usam `limit` e `offset` até que um domínio justifique paginação por cursor.
- A resposta de coleção usa `items`, `limit`, `offset` e `total`.
- A ordenação padrão deve ser determinística e documentada no endpoint.
- Filtros desconhecidos ou inválidos devem ser rejeitados, não ignorados silenciosamente.

## OpenAPI

Todo endpoint deve declarar:

- modelo de resposta;
- código de sucesso;
- `operation_id` estável e único;
- resumo, descrição e descrição da resposta;
- tag correspondente ao módulo;
- respostas de erro aplicáveis quando o tratamento comum for implementado.

O schema em `/openapi.json` é a representação executável do contrato. Alterações incompatíveis exigem nova versão da API ou decisão explícita registrada antes da implementação.

## Limites atuais

`GET /health` é operacional, não versionado, não consulta dependências externas e responde `200 OK` com `{"status":"ok"}`.

`POST /telegram/webhook` (`docs/TELEGRAM_ADAPTER.md`, `docs/MISSION_COMMANDS.md`) é o segundo endpoint existente e o primeiro autenticado: também operacional e fora de `/api/v1` (recebe um callback de integração externa, não um recurso de negócio). Autentica o transporte por segredo compartilhado no cabeçalho `X-Telegram-Bot-Api-Secret-Token`, usando `401` no envelope padrão quando ele não confere. Depois, a TASK-046 aceita identidade de usuário somente em chat privado direto e para `User` ativo; recusas dessa segunda camada retornam `204` sem efeitos nem retry e usam log sanitizado. Erro conhecido de domínio/validação também termina em `204`; falha inesperada continua subindo como `500`. Paginação, login genérico por usuário/senha (TASK-061) e um handler global de erros continuam pendentes das tarefas que introduzirem essas necessidades.
