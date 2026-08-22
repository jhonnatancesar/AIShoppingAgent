# TASK-101 — Área USER: Minha conta

Status: **Concluída, aprovada e publicada em `origin/main` (`c57f5ab`).**

## Objetivo

Criar `/app/account` para o usuário autenticado consultar e atualizar os dados
e preferências já persistidos na própria conta.

## Backend e autorização

- `GET /api/v1/account`, `PUT /api/v1/account/profile`,
  `PUT /api/v1/account/notification-preferences`, `POST` e `DELETE` em
  `/api/v1/account/telegram-link` exigem `WebSession`;
- perfil exige `Permission.PROFILE_MANAGE`; alertas exigem
  `Permission.NOTIFICATION_PREFERENCES_MANAGE`;
- a identidade vem exclusivamente da sessão: nenhum endpoint aceita `user_id`;
- schemas Pydantic explícitos ocultam IDs Telegram, credencial, hash, estado de
  cadastro e dados internos;
- CSRF continua automático nos dois `PUT` por `require_web_session`.

## Dados editáveis

- nome e e-mail opcional;
- lojas e categorias preferidas, usando o vocabulário persistido existente;
- alertas de queda de preço e preço-alvo atingido, compartilhados com o
  Telegram.

A resposta também informa username, papel, data de criação e apenas o estado
do vínculo Telegram. As opções disponíveis vêm do backend, para a tela não
duplicar o catálogo.

## Vínculo opcional com Telegram

- a Web funciona sem Telegram e nenhum fluxo USER passa a exigi-lo;
- a Web gera um token aleatório de alta entropia e retorna somente o comando
  temporário `/vincular <token>`;
- o banco persiste exclusivamente SHA-256 do token em
  `telegram_link_tokens`, com TTL de 10 minutos, uso único e invalidação da
  challenge anterior;
- a prova é aceita somente pelo webhook autenticado, em chat privado onde
  `chat.id == from.id`, antes da criação automática de usuário Telegram;
- o frontend nunca envia `telegram_user_id` ou `telegram_chat_id`;
- desvincular pela Web limpa apenas a identidade/destino Telegram, revoga
  sessões/tokens desse canal e preserva conta, WebSession, missões e ofertas;
- a tela diferencia `Não vinculado`, `Vinculação pendente` e `Vinculado`.

## Fora de escopo

IA, nova coleta, troca de username, papel, senha, exclusão/desidentificação de
conta e qualquer ação administrativa. A única migration é a tabela mínima e
específica da challenge temporária de vínculo.
Essas ações sensíveis exigem tarefas e fluxos próprios.

## Validação mínima

- leitura retorna somente campos seguros da própria sessão;
- atualização de perfil normaliza e valida os valores;
- preferências de notificação reutilizam os campos consumidos pelo Telegram;
- opção desconhecida falha antes de mutar o usuário;
- vínculo exige prova privada válida, expira e não pode ser reutilizado;
- desvincular preserva a conta Web e suas missões;
- tela React renderiza perfil, integração e alertas;
- Ruff, frontend lint/build e `git diff --check`.
