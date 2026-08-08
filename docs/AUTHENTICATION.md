# Autenticação por senha

A TASK-061 acrescenta autenticação por senha sem substituir as fronteiras das
TASKs 046 e 047. O Telegram primeiro autentica transporte, chat privado,
identidade e conta ativa; o RBAC autoriza o papel; a sessão por senha habilita
os comandos funcionais. Ownership continua obrigatório para todos os papéis.

## Fluxos

- `/senha`: cria a primeira senha ou altera a existente;
- `/entrar`: emite link de login e cria sessão de 12 horas após verificação;
- `/sair`: revoga imediatamente todas as sessões ativas daquele usuário;
- `/recuperar`: redefine a senha usando exclusivamente o Telegram privado já
  vinculado e revoga todas as sessões.

O link aponta para `/auth#<modo>:<token>`. Fragmentos não são enviados na
requisição GET; o JavaScript remove o fragmento do histórico e envia ao POST
somente token e campos de senha. O payload rejeita campos extras. `user_id`,
`telegram_user_id`, ação e papel sempre vêm do token bloqueado com
`SELECT ... FOR UPDATE`.

## Senhas e sessões

Argon2id usa `m=19456`, `t=2`, `p=1`, hash de 32 bytes e salt de 16 bytes.
Senhas possuem 15 a 128 caracteres, são normalizadas em NFC, aceitam Unicode,
espaços, colagem e gerenciadores, não usam regras de composição e são
comparadas com uma blocklist local de valores comuns/contextuais. Um login
bem-sucedido refaz o hash quando os parâmetros mudam.

Sessões têm TTL absoluto de 12 horas. Consulta não altera `expires_at`.
`revoked_at` nunca é limpo. Expiração e revogação falham fechadas.

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
