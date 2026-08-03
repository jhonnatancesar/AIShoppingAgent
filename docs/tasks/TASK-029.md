# TASK-029 — Implementar perfil USER

Status: Implementada; validação real pendente por ausência de credencial

## Objetivo

Implementar o perfil USER pelo AI Provider Manager usando Gemini.

## Escopo

- Integrar o SDK oficial `google-genai` pelo adaptador interno.
- Encaminhar somente requisições `USER` ao Gemini.
- Traduzir mensagens, respostas, quota e falhas sem expor detalhes sensíveis.
- Não implementar ADMIN, DEV, fallback, telemetria ou interpretação de intenção.

## Critério de aceite

Implementação e testes automatizados concluídos. Para concluir a TASK, falta uma
chamada autenticada ao Gemini com `AISHOPPING_GEMINI_API_KEY` fornecida fora do
repositório. O caminho real de credencial inválida já foi validado contra o
serviço e retornou erro sanitizado.

