# TASK-061 — Autenticação real por usuário e senha

Status: Concluída

## Objetivo

Acrescentar um fator de conhecimento ao Telegram privado já autenticado,
mantendo credenciais reutilizáveis no futuro sem aceitar senha no chat.

## Escopo implementado originalmente

Nota de estado atual: a TASK-078 consolidou criação e recuperação de senha no
comando público `/recuperar`. A menção histórica a `/senha` abaixo não descreve
mais um comando disponível no bot. A política atual, ajustada posteriormente,
é de 8 a 128 caracteres com maiúscula, minúscula, número e símbolo.

- `/senha`, `/entrar`, `/sair` e `/recuperar`;
- senha digitada exclusivamente no formulário HTTPS, nunca no Telegram;
- token aleatório de 256 bits, armazenado somente como SHA-256, uso único e
  TTL absoluto de 10 minutos;
- identidade e ação derivadas exclusivamente do token persistido;
- Argon2id com salt individual, `m=19456`, `t=2`, `p=1`, rehash oportunista,
  15 a 128 caracteres, Unicode NFC, espaços e blocklist (política original da
  TASK; substituída pela política atual indicada acima);
- sessão persistente vinculada a `user_id + telegram_user_id`, TTL absoluto de
  12 horas, sem renovação, refresh token ou JWT;
- logout imediato; troca e recuperação revogam todas as sessões;
- login: 5 falhas em 15 minutos geram cooldown de 15 minutos;
- token: no máximo 5 tentativas inválidas;
- links comuns: 5 emissões por 15 minutos; recuperação: 3 por hora e
  intervalo mínimo de 5 minutos;
- recuperação somente pela identidade privada do Telegram já vinculada;
- comandos funcionais exigem sessão; no estado atual, `/start`, `/ajuda`,
  `/cadastro`, `/entrar` e `/recuperar` permanecem acessíveis após
  TASK-046/047.

## Persistência

A migration `20260808_0008` cria `user_credentials`, `user_auth_sessions` e
`credential_action_tokens`, com FKs RESTRICT, `timestamptz`, hash único do
token e índices de lookup/expiração. Senha, token bruto e hash de senha nunca
são copiados para Telegram, logs, traces, métricas ou `audit_entries`.

## Fora de escopo

JWT, refresh token, OAuth, MFA, recuperação por e-mail, troca de identidade do
Telegram, integração de outro canal, mudança de papel e funcionalidades
comerciais da V2.

## Validação

- 553 testes em Python 3.14.6, 92,51% de cobertura e Ruff aprovado;
- migration upgrade, downgrade e novo upgrade no PostgreSQL 18 real;
- banco isolado confirmou consumo concorrente do mesmo token com exatamente um
  sucesso, replay terminal, TTLs, recuperação após nova conexão, logout,
  troca/recuperação e rate limiting persistente;
- API e worker Docker saudáveis; formulário validado em navegador e por HTTPS
  público, com CSP, `no-store`, senha oculta e mensagens genéricas;
- Bot API real autenticada e menu real atualizado com nove comandos;
- canários ausentes de logs, métricas, traces e auditoria;
- a simulação de update com identidade pessoal pelo túnel não foi realizada,
  pois a fronteira de execução bloqueou o envio desses dados; a integração
  do webhook permanece coberta por testes e a Bot API foi validada diretamente.

Próxima tarefa executável: TASK-048.
