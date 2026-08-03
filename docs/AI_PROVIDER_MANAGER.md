# AI Provider Manager

Todo acesso a IA passa por `AIProviderManager.generate`. Módulos de domínio não
podem importar SDKs nem chamar Gemini, OpenAI, Claude ou qualquer outro provedor
diretamente. `AIProvider` é uma porta interna usada apenas pelo manager.

## Contrato

- `AIRequest` possui UUID de correlação, perfil existente, propósito estável em
  `snake_case`, uma ou mais mensagens tipadas e horário UTC.
- Mensagens aceitam os papéis `system`, `user` e `assistant`.
- `AIResponse` mantém a correlação e informa provedor, modelo, conteúdo e horário.
- Requisições e respostas são imutáveis; conteúdo vazio, horário sem fuso e
  resposta de outra requisição são rejeitados.
- Erros de provedor expõem somente código sanitizado e indicação de nova
  tentativa. Respostas brutas, prompts, tokens e credenciais não pertencem ao
  erro.

Perfis previstos:

- `USER`: será implementado com Gemini na TASK-029.
- `ADMIN`: seleção da melhor IA disponível e fallback na TASK-030.
- `DEV`: seleção da melhor IA disponível e fallback na TASK-030.
- `PLUS`: futuro; não existe no contrato nem na V1.

Caso USER atinja o limite gratuito, a implementação futura deve informar que
tente novamente mais tarde. A TASK-028 define contratos; não instala SDKs, não
configura chaves, não seleciona modelos, não chama provedores e não adiciona
fallback ou telemetria.
