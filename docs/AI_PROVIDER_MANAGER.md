# AI Provider Manager

Todo acesso a IA deverá passar por um gerenciador único. Nenhum módulo de domínio poderá chamar Gemini, OpenAI ou Claude diretamente.

Perfis previstos:

- `USER`: Gemini.
- `ADMIN`: melhor IA disponível, com fallback.
- `DEV`: melhor IA disponível, com fallback.
- `PLUS`: futuro; múltiplas IAs, fora do escopo inicial.

Caso USER atinja o limite gratuito, o sistema deve informar que tente novamente mais tarde. Este documento define direção, não implementação.
