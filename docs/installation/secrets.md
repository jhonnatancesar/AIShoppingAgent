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
| Credencial César Core | `cesar_core_api_key` (container `api`, `compose.yaml`) / `cesar-core-client-dev` (execução nativa, `backend/.env`) -- mesmo valor, dois arquivos por ambiente de execução | sim | sim | não | não |
| Token do bot Telegram | `telegram_bot_token` | sim | não | sim | não |
| Segredo do webhook | `telegram_webhook_secret` | sim | não | não | não |
| Assinatura do controlador operacional | `ops_controller_secret` | sim | não | não | não |
| Assinatura do Windows Ops Agent | `windows_ops_agent_secret` | não | não | não | não |

A coluna "Collection Worker" descreve o que o worker nativo Windows
precisa (TASK-109: não é mais um serviço Docker, não monta `/run/secrets`
-- os mesmos secrets chegam por arquivo local no host, fora do Git/chat,
ver `docs/architecture/windows-collection-worker.md`). O worker usa a
mesma credencial do César Core para AI, grounding, Search e enrichment
(Fase E.1) -- não guarda mais chave própria de Firecrawl. Não recebe token
do bot nem segredo do webhook.

**DEC-104 (`v1.2.2`):** o worker nunca lê `.env` -- só variáveis de
ambiente de **Máquina** do Windows, geridas de forma reproduzível por
`scripts\manage_collection_worker_config.ps1` (`-Action
Install|Update|Status|Remove`), que audita/aplica exatamente os `*_FILE`
desta tabela relevantes ao worker (nunca inventa valor para secret
obrigatório ausente) e valida que `.secrets\` está com ACL restrita a
`Administrator`/`BUILTIN\Administrators`/`SYSTEM` -- ver tabela completa
de settings e procedimento de reprovisionamento em
`docs/architecture/windows-collection-worker.md`.

**`DEC-121` (2026-09-07):** até essa data, `compose.yaml` não encaminhava
nenhuma variável `AISHOPPING_CESAR_CORE_*` nem montava esta credencial no
serviço `api` -- gap real, encontrado só no preflight de PROD (o container
tentava `127.0.0.1:8100`, que dentro dele mesmo aponta para o próprio
container). Fechado com o secret `cesar_core_api_key`
(`${AISHOPPING_SECRETS_DIR:-./.secrets}/cesar_core_api_key`) e
`extra_hosts: host-gateway` no serviço `api` -- mesmo valor Bearer do
arquivo `cesar-core-client-dev` já usado em execução nativa (é uma decisão
manual do operador copiar o mesmo valor para os dois arquivos, não um
secret auto-gerado por `manage_secrets.py`).

`windows_ops_agent_secret` (TASK-109, fechamento) é consumido só pelo
`ops_controller` (Docker), para assinar chamadas ao Windows Ops Agent
(`http://host.docker.internal:8021`) -- precisa ser exatamente o mesmo
valor já gerado pelo Ops Agent em
`C:\ProgramData\AIShoppingAgent\secrets\ops-agent-secret`; os dois lados
não sincronizam sozinhos, copiar manualmente. `ops_controller_secret` e
`windows_ops_agent_secret` são os únicos dois secrets que o próprio
`ops_controller` monta (a coluna "API" acima cobre só quem CHAMA o
controller, assinando a requisição -- não quem a recebe).

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

### Serviços externos e Telegram

- Token do bot: atualizar `telegram_bot_token`, recriar API e worker, validar a
  Bot API e revogar a credencial antiga conforme o BotFather permitir.
- Segredo do webhook: atualizar `telegram_webhook_secret`, recriar a API e
  registrar novamente o webhook com exatamente o mesmo novo valor; validar a
  entrega antes de descartar a cópia antiga.

### César Core (TASK-118)

`cesar-core-client-dev`: credencial de aplicação (Bearer) emitida **pelo
próprio César Core** para este consumidor (`gg_oferta`), nunca a credencial
upstream do OmniRoute nem a senha do Control Plane administrativo do Core.
Para rotacionar: emitir uma nova credencial no Control Plane/API Admin do
Core, atualizar o arquivo local, recriar o(s) consumidor(es) autorizado(s) e
só então revogar a credencial antiga no lado do Core. Este repositório não
controla quota nem capabilities — isso é decidido no `cesar-core`
(`docs/architecture/cesar-core-integration.md`).

### Controlador operacional e Windows Ops Agent (TASK-109)

- `ops_controller_secret`: gerar novo valor (`python -m scripts.manage_secrets
  init --overwrite` ou `secrets.token_urlsafe(48)` equivalente), atualizar
  `.secrets/ops_controller_secret`, recriar `api` (quem assina as chamadas) e
  `ops_controller` (quem verifica a assinatura) juntos -- é HMAC de segredo
  compartilhado, os dois lados precisam trocar na mesma janela, sem período de
  sobreposição com valores diferentes. Validar uma chamada real
  (`status`/`start`/`restart`) antes de considerar concluído.
- `windows_ops_agent_secret`: só o próprio Windows Ops Agent gera esse valor,
  na primeira inicialização (`_load_or_create_secret`,
  `ops_agent/collection_worker_ops_agent.py`) -- nunca `manage_secrets.py`.
  Para rotacionar: parar o serviço `AIShoppingAgentOpsAgent`
  (`python ops_agent\collection_worker_ops_agent.py stop`), apagar
  `C:\ProgramData\AIShoppingAgent\secrets\ops-agent-secret`, iniciar de novo
  (`start`) para que um novo valor seja gerado, copiar esse novo valor
  manualmente para `.secrets/windows_ops_agent_secret` (os dois lados nunca
  sincronizam sozinhos) e recriar `ops_controller`. Validar
  `status`/`start`/`restart` reais antes de remover qualquer anotação do
  valor antigo.

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
