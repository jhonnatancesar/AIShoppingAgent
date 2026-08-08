# TASK-047 — Adicionar autorização

Status: Concluída

## Objetivo

Adicionar autorização RBAC fail-closed à V1, preservando um único papel por
usuário e isolamento obrigatório por proprietário.

## Escopo

- manter `users.role` com exatamente um valor entre `USER`, `ADMIN` e `DEV`;
- implementar herança de permissões `USER ⊂ ADMIN ⊂ DEV`, sem igualar os
  conceitos;
- novos usuários do Telegram recebem exclusivamente `USER`;
- `ADMIN` e `DEV` só podem ser atribuídos manualmente e nunca por entrada
  pública;
- `DEV` é o superusuário técnico da V1 e usa o perfil interno DEV;
- ownership continua obrigatório para todos os papéis;
- recursos alheios são indistinguíveis de inexistentes;
- autorização ocorre depois da TASK-046 e antes de IA ou efeito funcional;
- recusa permite somente auditoria append-only sanitizada e termina o webhook
  em `204`;
- ajustar o proprietário atual de `ADMIN` para `DEV` por operação one-shot,
  vinculada ao UUID verificado e auditada, sem código de startup ou regra geral.

## Fora do escopo

- migration estrutural, múltiplos papéis ou `user_roles`;
- `FREE`, `PLUS`, `PRO`, assinatura ou entitlements;
- gestão de papéis pelo Telegram, autopromoção ou bypass cross-user;
- funcionalidades administrativas artificiais;
- usuário/senha/JWT/MFA (TASK-061), segredos (TASK-048) ou resiliência
  (TASK-049).

## Critério de aceite

Matriz e herança de permissões testadas; papel inválido falha fechado; novos
usuários permanecem `USER`; ownership protegido em missões, recomendação,
comparação, confirmação e trilha; intenção pendente adulterada não produz
efeito; recusas são auditadas sem dados sensíveis; proprietário promovido por
UUID em operação one-shot; PostgreSQL 18, API Docker e Telegram reais, pipeline,
revisão e documentação aprovados.

## Resultado

- adicionada política central fail-closed com permissões concretas e herança
  estrita `USER ⊂ ADMIN ⊂ DEV`;
- autorização aplicada no webhook depois da autenticação e antes de IA ou
  domínio, com `204` terminal e auditoria append-only sanitizada na recusa;
- novo usuário do Telegram permanece USER e nenhuma entrada pública atribui
  ADMIN/DEV;
- ownership reforçado em missão, recomendação, comparação, confirmação e
  trilha, sem bypass para ADMIN/DEV;
- proprietário promovido uma única vez de ADMIN para DEV por UUID validado,
  com `user.role_changed`, sem migration, startup ou regra genérica;
- múltiplos papéis, planos e gestão de roles continuam reservados à V2.

## Validação

- testes direcionados cobriram hierarquia, fail-closed, auditoria, perfil de
  IA, ownership e intenção pendente adulterada;
- PostgreSQL 18 real confirmou hierarquia, isolamento e auditoria, revertendo
  todos os dados temporários;
- banco local alinhado de `20260808_0006` para o head `20260808_0007` após a
  validação detectar a migration pendente; recomendação, comparação e
  confirmação reais foram aprovadas depois da correção;
- API e worker Docker foram reconstruídos e permaneceram saudáveis;
- webhook HTTP real respondeu `204` para o proprietário DEV e a Telegram Bot
  API confirmou a integração real;
- pipeline completo aprovado em Python 3.14.6: 508 testes, 92,47% de cobertura,
  Ruff, Alembic com head único e Docker Compose válidos;
- nenhuma migration estrutural foi criada.

Próxima tarefa executável: TASK-061; depois, TASK-048 (`DEC-033`).

