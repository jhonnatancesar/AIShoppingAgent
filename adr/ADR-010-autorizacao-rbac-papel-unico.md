# ADR-010 — Autorizar por papel único com ownership obrigatório

## Status

Aceita em 2026-08-08 pela TASK-047.

## Contexto

A TASK-046 autentica o transporte e a identidade privada do Telegram, mas não
decide quais ações uma conta ativa pode executar. O modelo existente possui um
único `users.role`, e ampliar a V1 com papéis múltiplos ou planos comerciais
aumentaria o MVP sem necessidade.

## Decisão

A V1 mantém exatamente um papel entre `USER`, `ADMIN` e `DEV` e centraliza a
matriz de permissões em `app.authorization`, com herança estrita
`USER ⊂ ADMIN ⊂ DEV`. Papel ausente ou desconhecido falha fechado. Novos
usuários do Telegram são sempre USER; ADMIN e DEV só podem ser atribuídos
manualmente.

Ownership é uma condição adicional e obrigatória para todos os papéis. DEV é
o superusuário técnico da V1, mas não recebe acesso implícito aos dados
privados de outros usuários. Recusas no webhook são terminais em `204` e
produzem somente auditoria append-only sanitizada.

## Consequências

- autenticação e autorização permanecem fronteiras distintas;
- nenhum payload ou comando pode promover uma conta;
- recursos de outro dono são indistinguíveis de recursos inexistentes;
- o proprietário foi promovido por uma operação one-shot auditada, sem
  migration ou lógica de startup;
- múltiplos papéis, planos, entitlements e gestão de papéis ficam para a V2;
- login por usuário e senha continua exclusivamente na TASK-061.
