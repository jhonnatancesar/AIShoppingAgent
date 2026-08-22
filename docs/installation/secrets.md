# Gestão de secrets

A V1 usa arquivos locais montados pelo Docker Compose em `/run/secrets`. Esse
desenho reduz exposição por ambiente, imagem e inspeção de contêiner, mas não é
um cofre externo nem criptografa os arquivos no host. No Ubuntu Server, a
proteção em repouso depende do proprietário do diretório e de permissões `0700`
para o diretório e `0600` para cada arquivo.

## Inventário e menor privilégio

| Secret | Arquivo | API | Collection Worker | Telegram Notifier | PostgreSQL |
| --- | --- | --- | --- | --- | --- |
| Senha PostgreSQL | `postgres_password` | sim | sim | sim | sim |
| Chave Gemini USER | `gemini_api_key_user` | sim | não | não | não |
| Chave Gemini ADMIN/DEV | `gemini_api_key_admin_dev` | sim | sim (TASK-063) | não | não |
| Chave Groq | `groq_api_key` | sim | sim (TASK-063) | não | não |
| Token do bot Telegram | `telegram_bot_token` | sim | não | sim | não |
| Segredo do webhook | `telegram_webhook_secret` | sim | não | não | não |
| Assinatura do controlador operacional | `ops_controller_secret` | sim | não | não | não |

O `collection_worker` (TASK-063) usa a chave Gemini ADMIN/DEV — a mesma
cascata premium/Groq/gratuito já usada pela `api` (nunca a chave/cota do
perfil `USER`, reservada a conversas reais no Telegram) — só para
normalizar título de exibição e classificar a correspondência
missão↔oferta antes de um alerta. Não recebe token do bot, segredo do
webhook nem a chave Gemini `USER`.

`POSTGRES_USER`, nomes de banco, URLs e nomes de modelo não são tratados como
secrets. Senhas de usuário e tokens de ação da TASK-061 permanecem no banco
segundo `docs/architecture/authentication.md`; não são configuração do Compose.

## Fonte única e precedência

Cada secret possui o campo direto `AISHOPPING_<NOME>` e o campo de caminho
`AISHOPPING_<NOME>_FILE`.

- Produção aceita somente `*_FILE`. Um valor direto é rejeitado mesmo quando
  não existe arquivo configurado.
- Se valor direto e `*_FILE` existirem simultaneamente, qualquer ambiente
  falha fechado; não há escolha silenciosa.
- Desenvolvimento permite `.env` direto ou arquivo, nunca ambos para o mesmo
  secret.
- Arquivo ausente, vazio, multilinha, ilegível ou maior que 16 KiB é inválido.
- Uma única quebra de linha final é removida; outros espaços são preservados.

No Compose, somente os caminhos ficam no ambiente. Os valores são montados
como arquivos em `/run/secrets` e não entram no build context, nas layers ou no
filesystem persistente da aplicação.

## Preparação local

Para migrar os valores dos `.env` locais antigos sem exibi-los nem removê-los:

```powershell
python -m backend.scripts.manage_secrets migrate
python -m backend.scripts.manage_secrets check
```

Em uma instalação nova, o modo interativo gera a senha do banco e os segredos
internos de webhook/controlador, e lê as credenciais externas com entrada oculta:

```powershell
python -m backend.scripts.manage_secrets init
python -m backend.scripts.manage_secrets check
```

O diretório padrão é `.secrets`, ignorado pelo Git e pelo build. Nunca passe um
secret como argumento de terminal, não use `echo`/`cat` e não inclua valores em
documentação, logs, relatórios ou tickets. No Ubuntu, valide também:

```bash
stat -c '%a %n' .secrets .secrets/*
```

O esperado é `700` no diretório e `600` nos arquivos. O Compose usa
`AISHOPPING_SECRETS_DIR` somente como caminho não secreto.

## Rotação

Regra geral: obter a nova credencial no provedor, atualizar o arquivo de forma
controlada sem imprimir o valor, recriar apenas os consumidores autorizados,
validar o fluxo e só então revogar ou remover a cópia antiga. O Compose não
recarrega automaticamente secrets em processos existentes.

### PostgreSQL

**Banco/volume novo:** a imagem oficial consome `POSTGRES_PASSWORD_FILE`
somente durante a inicialização e cria o papel com esse valor.

**Banco/volume existente:** trocar apenas o arquivo não altera a senha do papel.
A operação controlada deve:

1. abrir uma sessão administrativa já autenticada e executar `ALTER ROLE` sem
   colocar a senha na linha de comando ou em logs;
2. atualizar atomicamente `postgres_password`;
3. recriar API e worker para que leiam o novo arquivo;
4. validar uma nova conexão e `/ready`;
5. remover com segurança qualquer cópia operacional antiga.

O PostgreSQL mantém uma única senha SCRAM por papel; portanto a troca pode
exigir uma janela coordenada. Rotação sem interrupção por papéis duplicados ou
cofre dinâmico não pertence à V1.

### Provedores e Telegram

- Gemini/Groq: emitir nova chave, atualizar o arquivo da API, recriar a API,
  validar pelo AI Provider Manager e revogar a anterior.
- Token do bot: atualizar `telegram_bot_token`, recriar API e worker, validar a
  Bot API e revogar a credencial antiga conforme o BotFather permitir.
- Segredo do webhook: atualizar `telegram_webhook_secret`, recriar a API e
  registrar novamente o webhook com exatamente o mesmo novo valor; validar a
  entrega antes de descartar a cópia antiga.

## Detecção de vazamento

O pipeline fixa Gitleaks `8.29.1`; o instalador verifica SHA-256 publicado para
Windows x64 e Linux x64 antes de extrair. São examinados working tree, arquivos
versionados e todo o histórico Git relevante. Um repositório temporário com
secret-canário gerado em memória deve ser recusado para provar que o scanner
está ativo. Relatórios persistentes não são gerados e descobertas não são
impressas, evitando republicar um valor detectado.

```powershell
python scripts/install_gitleaks.py
python scripts/scan_secrets.py
```

Se um secret real for encontrado, interrompa o fluxo, revogue/rotacione primeiro
no provedor e investigue o alcance. Reescrita de histórico é destrutiva e exige
decisão explícita; remover apenas o arquivo no commit mais recente não revoga a
credencial já exposta.

## Limites da V1

Vault, AWS/Azure/GCP Secret Manager, secrets dinâmicos, rotação automática e
criptografia gerenciada em repouso ficam fora desta TASK. O objetivo da V1 é
evitar vazamento, aplicar menor privilégio, manter configuração reproduzível e
permitir rotação manual segura em um único Ubuntu Server.
