# TASK-029 — Implementar perfil USER

Status: Concluída

## Objetivo

Implementar o perfil USER pelo AI Provider Manager usando Gemini.

## Escopo

- Integrar o SDK oficial `google-genai` pelo adaptador interno.
- Encaminhar somente requisições `USER` ao Gemini.
- Traduzir mensagens, respostas, quota e falhas sem expor detalhes sensíveis.
- Não implementar ADMIN, DEV, fallback, telemetria ou interpretação de intenção.

## Critério de aceite

Implementação e testes automatizados concluídos. Uma chamada autenticada real foi
executada pelo `AIProviderManager` contra `gemini-3.6-flash`, retornando resposta
não vazia e o conteúdo esperado. O caminho real de credencial inválida também foi
validado e retornou erro sanitizado. A credencial permaneceu somente no `.env`
ignorado pelo Git.

