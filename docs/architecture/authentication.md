# Autenticação por senha

A TASK-061 acrescenta autenticação por senha sem substituir as fronteiras das
TASKs 046 e 047. O Telegram primeiro autentica transporte, chat privado,
identidade e conta ativa; o RBAC autoriza o papel; a sessão por senha habilita
os comandos funcionais. Ownership continua obrigatório para todos os papéis.

## Fluxos

- ao concluir `/cadastro`, o bot emite automaticamente o link para criar a
  primeira senha;
- `/recuperar`: cria a primeira senha (`SET_PASSWORD`) quando ainda não existe
  `UserCredential`; quando a credencial já existe, redefine a senha
  (`RECOVER_PASSWORD`) e revoga todas as sessões;
- `/entrar`: emite link de login e cria sessão de 12 horas após verificação;
- `/sair`: revoga imediatamente todas as sessões ativas daquele usuário;

Não existe comando público separado para senha. Criação e recuperação começam
sempre por `/recuperar`, exclusivamente no Telegram privado já vinculado.

O link aponta para `/auth#<modo>:<token>`. Fragmentos não são enviados na
requisição GET; o JavaScript remove o fragmento do histórico e envia ao POST
somente token e campos de senha. O payload rejeita campos extras. `user_id`,
`telegram_user_id`, ação e papel sempre vêm do token bloqueado com
`SELECT ... FOR UPDATE`.

## Senhas e sessões

Argon2id usa `m=19456`, `t=2`, `p=1`, hash de 32 bytes e salt de 16 bytes.
Senhas possuem 8 a 128 caracteres, são normalizadas em NFC, aceitam Unicode,
espaços, colagem e gerenciadores e exigem pelo menos uma letra maiúscula, uma
letra minúscula, um número e um símbolo. Uma blocklist local rejeita valores
comuns/contextuais. Um login bem-sucedido refaz o hash quando os parâmetros
mudam.

Sessões têm TTL absoluto de 12 horas. Consulta não altera `expires_at`.
`revoked_at` nunca é limpo. Um novo login substitui qualquer sessão anterior
da mesma conta. Expiração e revogação falham fechadas.

Cada conclusão válida publica `authentication.completed.v1` na mesma
transação da senha ou sessão. O consumidor `telegram_auth_notifications_v1`
confirma no chat privado: senha criada, login realizado, senha alterada ou
senha recuperada. `/sair` já confirma o encerramento na própria resposta do
comando. Essas mensagens não contêm senha, token ou hash e não são afetadas
pelas preferências de alertas de preço.

O `telegram_notifier` verifica sessões com lock concorrente. Trinta minutos
antes do vencimento publica `authentication.session_expiring.v1`; ao atingir o
TTL publica `authentication.session_expired.v1` e orienta novo `/entrar`.
Marcadores persistentes são atualizados na mesma transação do evento, portanto
restart ou ciclos repetidos não recriam o aviso. Sessão revogada ou aviso que
ficou obsoleto é consumido como `skipped`. A migration `20260809_0002` marca
sessões preexistentes para não enviar histórico retroativo.

## Tokens e limites

Tokens usam `secrets.token_urlsafe(32)` e somente seu SHA-256 é persistido.
Expiram em 10 minutos, terminam em `consumed_at` ou `invalidated_at` e aceitam
no máximo cinco tentativas inválidas. O consumo e o efeito (senha, revogação
ou sessão) ocorrem na mesma transação.

Login permite cinco falhas numa janela de 15 minutos e aplica cooldown de 15
minutos, sem bloqueio permanente. Links comuns permitem cinco emissões em 15
minutos. Recuperação permite três por hora e exige cinco minutos entre links.
Mensagens externas não distinguem conta, token, senha ou cooldown.

## Operação

`AISHOPPING_AUTH_PUBLIC_BASE_URL` precisa ser HTTPS fora de localhost. Em
produção, configure um domínio TLS estável antes de oferecer os comandos. O
e-mail atual não é verificado e nunca recupera conta. Perda simultânea da senha
e do Telegram exige operação manual e controlada pelo proprietário.

Senha, hash, token, payload e material de autenticação não podem aparecer em
logs, traces, métricas ou auditoria. As rotas `/auth` e `/auth/actions` são
excluídas do tracing automático; métricas usam apenas rota normalizada.

Action tokens expirados/resolvidos ficam elegíveis à limpeza manual após 24
horas; sessões expiradas/revogadas, após 30 dias. Não há scheduler de limpeza
na V1; a inspeção de expiração pelo notifier serve somente para publicar os
dois avisos. A
desidentificação controlada remove credencial, tokens e sessões da conta em uma
única transação, preservando somente auditoria sanitizada. Procedimentos e
limitações estão em `docs/architecture/privacy.md`.
