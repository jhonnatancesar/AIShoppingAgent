# TASK-061 — Autenticação real por usuário e senha

Status: Pendente

## Objetivo

Permitir que um usuário se autentique no bot por usuário e senha, para
confirmar sua identidade além do `telegram_user_id` já resolvido pela
TASK-056 — por exemplo, para vincular a mesma conta a um futuro canal além
do Telegram.

## Contexto

Registrada em `DEC-019` a partir de um pedido do usuário ao detalhar os
campos do cadastro inicial da TASK-060. Retirada de lá porque autenticação
por senha exige desenho de segurança próprio (hashing, verificação,
recuperação de conta), não é "dado não sensível" e não deve ser implementada
apressadamente dentro de outro cadastro.

Pela `DEC-033`, esta tarefa foi retirada da V1.2 e inserida no fluxo principal
de segurança. Deve ser executada imediatamente depois da TASK-047 e antes da
TASK-048.

## Escopo (a definir em detalhe na validação desta TASK)

- Algoritmo de hashing de senha (ex.: Argon2/bcrypt), nunca texto puro.
- Fluxo de definição/alteração de senha.
- Fluxo de verificação (como o usuário se autentica pelo Telegram usando
  usuário+senha, já que a identidade do Telegram já é conhecida).
- Recuperação de conta / redefinição de senha.
- Motivo concreto de negócio para exigir isso além do `telegram_user_id`
  (ex.: preparar um canal futuro fora do Telegram).

## Fora de escopo

- Não é a TASK-060 (perfil de IA por papel, cadastro não sensível,
  placeholder de upgrade).

## Critério de aceite

A definir na validação desta TASK. Ela se torna a próxima tarefa executável
assim que a TASK-047 for concluída.
