# TASK-117 — Verificação de e-mail via Cloudflare Access (One-Time PIN)

Status: **Pré-flight concluído, desenho aprovado pendente.** Nenhum
código de aplicação, migration ou endpoint escrito ainda. Dependência
`PyJWT[crypto]` instalada e declarada em `backend/requirements.txt`
(único passo de preparação já executado nesta fase).

Release alvo: **v1.2.3** (não v1.2 — tag ainda não criada). Reabre, de
forma restrita, a decisão `DEC-072` (confirmação/verificação de e-mail
adiada para a V2): esta TASK antecipa **apenas** a verificação de posse
via Cloudflare Access, não qualquer outra capability de e-mail (envio de
notificação por e-mail, opt-in de e-mail marketing, recuperação de conta
por e-mail continuam fora de escopo e continuam adiados).

## Dependência obrigatória

- Domínio público `https://ggoferta.com` e Cloudflare Tunnel já
  publicados pelo usuário (fora desta TASK) apontando para
  `http://127.0.0.1:8000`.
- Uma Cloudflare Access Application para `ggoferta.com`, uma Policy
  estável e um Access Group gerenciado pelo GG Oferta — provisionamento
  manual único no painel Cloudflare, documentado nesta TASK, não
  automatizado por código.
- Estende o cadastro por Telegram (`app/users/registration.py`) e o
  modelo `User` (`app/users/models.py`) já existentes — não cria um
  segundo sistema de identidade.

## Dependências técnicas a instalar

- `PyJWT[crypto]>=2.13,<3.0` — validação criptográfica local (RS256 +
  JWKS) do Access JWT do Cloudflare Access, biblioteca oficialmente
  recomendada pela documentação da Cloudflare para Python. **Já
  instalada e declarada em `backend/requirements.txt` no ambiente DEV**
  (não commitado ainda, faz parte desta TASK). **Precisa ser instalada
  na PROD no momento do deploy desta TASK** — mesmo mecanismo já usado
  para toda dependência do projeto (`pip install -r
  backend/requirements.txt`), nenhum passo manual novo. Não há nada a
  instalar na PROD antes disso; não é uma ação separada desta fase.
- Nenhuma outra biblioteca nova identificada até aqui (cliente HTTP para
  a API Cloudflare reaproveita `httpx`, já dependência do projeto).

## Escopo exclusivo

- E-mail obrigatório para **novos** cadastros pelo Telegram; normalização
  (trim + lowercase seguro, sem regras específicas de provedor) e
  unicidade (`User.email`).
- Estado do e-mail: ausente / `PENDING` / `VERIFIED`
  (`email_verified_at`), mais estado de sincronização com o Cloudflare
  Access separado do estado de verificação (`Cloudflare Allowed` ≠
  `GG Oferta Verified`).
- Sincronização assíncrona (via outbox de `Event`, nunca dentro da
  transação de cadastro) do e-mail `PENDING` para o Access Group via API
  Cloudflare, com retry e sem duplicar entradas.
- Endpoint/callback dedicado que recebe a identidade autenticada do
  Cloudflare Access (`Cf-Access-Jwt-Assertion`), valida criptograficamente
  (assinatura RS256 via JWKS, `aud`, `iss`, `exp`), localiza o `User`
  `PENDING` correspondente por e-mail normalizado e marca
  `email_verified_at`, de forma idempotente.
- Remoção/desautorização do e-mail no Access Group quando o usuário for
  bloqueado/desativado/removido, e quando um e-mail é trocado (o antigo
  só é removido depois do novo confirmado).
- Mecanismo de reconciliação (Status/DryRun/Reconcile) entre o GG Oferta
  e o Access Group.
- Migration Alembic aditiva para os novos campos/tabela de estado —
  desenhada nesta TASK, não obrigatoriamente aplicada nesta fase.
- Atualização de `AISHOPPING_AUTH_PUBLIC_BASE_URL` e auditoria de tudo
  que depende dela para `https://ggoferta.com`.
- Textos do Telegram atualizados para o novo passo obrigatório, sem
  jargão técnico (JWT/AUD/JWKS) para o usuário final.

## Fora de escopo

Implementar SMTP/serviço de envio de e-mail próprio (Resend/Brevo/SES/
SendGrid) ou qualquer challenge de código de 6 dígitos próprio — o OTP é
inteiramente do Cloudflare Access. Remover ou substituir o login
próprio do GG Oferta (SSO fica para estudo futuro separado). Criar
tag/release `v1.2.3` ou fazer deploy em PROD nesta fase. Contratar
qualquer produto pago da Cloudflare — permanece no plano Zero Trust
Free. Marcar e-mails de usuários já existentes como `VERIFIED`
automaticamente por migration. Notificação por e-mail de fato,
recuperação de conta por e-mail, e demais itens de `DEC-072` que
seguem adiados para a V2.
