# Autorização da V1

A TASK-047 aplica RBAC depois da autenticação mínima do Telegram e antes de
IA, leitura ou mutação funcional. A V1 mantém exatamente um `users.role` por
usuário: `USER`, `ADMIN` ou `DEV`.

## Hierarquia

A política central vive em `app.authorization` e implementa herança estrita:

`USER ⊂ ADMIN ⊂ DEV`

`ADMIN` recebe as permissões normais de `USER` e somente capacidades
administrativas que realmente existirem. `DEV` recebe as permissões de
`USER` e `ADMIN`, além das capacidades técnicas existentes e do perfil interno
de IA DEV. A herança não torna os papéis semanticamente iguais.

Papel ausente, desconhecido ou não representado por `UserRole` falha fechado.
O papel nunca é aceito de payload, texto, comando, cadastro ou parâmetro do
Telegram. Todo usuário provisionado automaticamente começa como `USER`.
`ADMIN` e `DEV` exigem atribuição manual e controlada.

## Ownership

Permissão por papel não substitui propriedade. USER, ADMIN e DEV acessam
somente os próprios recursos nas funcionalidades normais. Consultas de missão,
recomendação e comparação filtram pelo proprietário; confirmação e trilha
mantêm o mesmo vínculo. Recurso inexistente e recurso de outro usuário recebem
a mesma recusa, sem facilitar enumeração.

Não existe ferramenta cross-user, autopromoção ou bypass de ownership na V1.
Uma futura ferramenta administrativa exigirá permissão própria e outra TASK.

## Recusas

Uma recusa no webhook encerra com `204`, sem resposta funcional ou retry do
Telegram. O único efeito permitido é uma entrada append-only
`authorization.denied`, com permissão, motivo fechado e papel sanitizado.
Texto, payload, token e demais dados sensíveis não entram nessa auditoria nem
nos logs operacionais.

## Proprietário da V1

O proprietário existente foi alterado uma única vez de ADMIN para DEV durante
a execução da TASK-047. A operação usou o UUID explicitamente validado,
confirmou conta ativa e vínculo privado do Telegram, alterou somente aquele
registro e gravou `user.role_changed` em `audit_entries` na mesma transação.
Não há migration, script persistente, inicialização automática ou regra geral
`ADMIN -> DEV`.

Múltiplos papéis, `user_roles`, planos `FREE`/`PLUS`/`PRO`, assinatura,
entitlements e gestão administrativa de papéis permanecem na V2.
