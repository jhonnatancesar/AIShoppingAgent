# Catálogo de textos visíveis ao usuário

Catálogo aprovado e implementado pela TASK-087. Os campos entre `{chaves}` são valores dinâmicos.
Logs, nomes técnicos de exceção e mensagens exclusivamente internas não entram.

## Primeiro contato, sessão e ajuda

### Primeiro contato

```text
👋 Opa! Eu sou o Cláudio, seu assistente de compras.

Posso procurar produtos em várias lojas, comparar preços e acompanhar ofertas pra você.

Pra começar, vamos criar seu acesso.

Use /cadastro e eu te guio por aqui. 🎯
```

### Usuário retornando sem sessão

```text
👋 Opa, você voltou!

Sua conta já está cadastrada. Agora só precisamos entrar novamente.

Use /entrar para acessar sua conta.

Esqueceu a senha? Sem problema.
Use /recuperar.
```

### Sessão obrigatória

```text
🔒 Sua sessão não está ativa.

Use /entrar para acessar sua conta.

Esqueceu a senha?
Use /recuperar.
```

### `/ajuda`

```text
📖 Aqui está o que posso fazer por você:

🛒 COMPRAS
/criar_missao — criar uma nova missão
/cancelar_missao — cancelar uma missão existente
/listar_missoes — listar missões ativas, pausadas e canceladas
/missao — entender como funcionam as missões
/editar_missao — mudar lojas ou preço-alvo de uma missão pausada

👤 CONTA
/cadastro — completar seu perfil
/entrar — acessar sua conta
/recuperar — criar ou recuperar sua senha
/sair — encerrar a sessão

⚙️ CONFIGURAÇÕES
/preferencias — configurar notificações
/privacidade — entender o uso e a proteção dos seus dados
```

### `/missao`

```text
🛒 Para criar uma missão, use /criar_missao.

Depois é só me dizer o que você quer acompanhar.

Exemplos:
• Ryzen 7 9800X3D até R$ 3.000
• RTX 5070 Ti na Kabum
• mouse gamer
```

### Texto não reconhecido

```text
Quer criar uma missão?
Use /criar_missao.

Para ver tudo o que posso fazer, use /ajuda.
```

## Cadastro

Bloqueios para cadastro já existente:

```text
✅ Você já está cadastrado e autenticado neste Telegram.
```

```text
📋 Você já tem cadastro neste Telegram.

Use /entrar para acessar sua conta.

Se ainda não criou sua senha ou esqueceu, use /recuperar.
```

```text
Vamos cadastrar você! Qual nome de usuário você quer usar?
```

```text
Certo! Agora seu e-mail (ou responda "pular" para deixar em branco).
```

```text
Quais lojas você prefere?

1 — Kabum
2 — Pichau
3 — Terabyte
4 — Amazon
5 — Todas

Digite os números separados por vírgula (ex.: 1,2), use 5 para todas ou responda "pular".
```

```text
Por último: quais categorias você mais compra?

1 — Hardware / Componentes de PC
2 — Periféricos
3 — Computadores / PC Gamer montado
4 — Notebooks
5 — Monitores
6 - Celulares e Smartphones
7 - TV, Áudio e Vídeo
8 - Video Games e Consoles
9 - Cadeiras e Móveis Gamer/Escritório
10 - Casa Inteligente e Automação
11 — Eletrodomésticos e Eletroportáteis
12 — Câmeras e Drones
13 — Redes e Conectividade
14 — Segurança (câmeras, alarmes)
15 — Geek e Colecionáveis
16 - Todas

Digite os números separados por vírgula (ex.: 1,4,8), use 16 para todas ou responda "pular".
```

```text
✅ Cadastro confirmado!

Agora crie sua senha pelo link abaixo.
```

Erros possíveis:

```text
Nome de usuário inválido: precisa ter entre 1 e 32 caracteres.
Nome de usuário não pode começar com "/" nem conter espaços.
O nome de usuário "{username}" já está em uso. Escolha outro.
E-mail inválido. Tente de novo ou responda "pular".
Não entendi as lojas escolhidas.

Use os números de 1 a 4 separados por vírgula, 5 para todas ou responda "pular".

Não entendi as categorias escolhidas.

Use os números de 1 a 15 separados por vírgula, 16 para todas ou responda "pular".
```

## Autenticação

```text
✅ Você já está autenticado. Use /sair se quiser encerrar a sessão.
👋 Sessão encerrada.

Use /entrar quando quiser acessar novamente.
Complete primeiro seu nome de usuário com /cadastro.
Muitas solicitações. Tente novamente mais tarde.
Não foi possível gerar o link. Verifique seu cadastro.
```

Template do link:

```text
{ícone} {ação}
{url}

Esse link é pessoal, de uso único e expira em 10 minutos.
```

Rótulos possíveis: `🔑 Entrar`, `🔐 Criar senha` e `🔐 Recuperar senha`.

Página web do link:

```text
AIShoppingAgent — acesso seguro
Acesso seguro
Este link é pessoal, descartável e expira em 10 minutos.
Senha
Confirmar senha
Continuar
```

Respostas finais da página:

```text
Senha criada. Volte ao Telegram e use /entrar.
Login concluído. Você pode voltar ao Telegram.
✅ Senha alterada.

As sessões anteriores foram encerradas.
Volte ao Telegram e use /entrar novamente.

✅ Senha redefinida.

As sessões anteriores foram encerradas.
Volte ao Telegram e use /entrar novamente.
Não foi possível concluir. Solicite um novo link no Telegram.
```

### Upgrade

```text
🔒 Mudar de usuário/perfil — em breve.
```

Conclusões assíncronas:

```text
✅ Senha criada com sucesso!

Agora use /entrar para acessar sua conta.
```

```text
✅ Login realizado com sucesso!

Sua sessão ficará ativa por 12 horas.
```

```text
✅ Senha alterada com sucesso!

As sessões anteriores foram encerradas — use /entrar novamente.
```

```text
✅ Senha recuperada com sucesso!

As sessões anteriores foram encerradas — use /entrar novamente.
```

```text
⏳ Sua sessão expira em breve

{dd/mm/aaaa às hh:mm} — horário de Brasília

Depois disso, use /entrar para acessar novamente.
```

```text
🔒 Sua sessão expirou.

Use /entrar para acessar novamente.
```

Erros da página de autenticação:

```text
Operação indisponível.
Link inválido ou expirado.
Não foi possível concluir a operação.
Credenciais inválidas.
Tente novamente mais tarde.
As senhas informadas não conferem.
A senha precisa ter pelo menos 8 caracteres.
A senha pode ter no máximo 128 caracteres.
Essa senha é muito comum ou previsível.
A senha precisa ter pelo menos uma letra maiúscula.
A senha precisa ter pelo menos uma letra minúscula.
A senha precisa ter pelo menos um número.
A senha precisa ter pelo menos um símbolo.
```

## Criação de missão

```text
Beleza. Me diga o que você quer procurar. Você tem 10 minutos para enviar a descrição.
⌛ O tempo para descrever a missão acabou.

Use /criar_missao quando quiser começar novamente.
Não consegui interpretar a missão agora. Use /criar_missao para tentar novamente.
```

Escolha de lojas:

```text
🏪 Em quais lojas você quer que eu procure?

1 — Pichau
2 — Terabyte
3 — Amazon
4 — Kabum
5 — Todas

Digite os números separados por vírgula (ex.: 1,3) ou use 5 para todas.
```

```text
Não entendi essa opção.

Use os números de 1 a 4 separados por vírgula ou 5 para todas.
```

Confirmação:

```text
🔎 Confirmar nova missão?

Produto: "{produto}"
🎯 Alvo: {preço}                         [quando houver]
🏪 Lojas: {lojas}

1 — Confirmar
2 — Cancelar

Você também pode responder "sim" ou "não".
```

Sucesso:

```text
✅ Missão criada!

🔎 Produto: {produto}
🎯 Alvo: {preço}                         [quando houver]
🏪 Lojas: {lojas}

Vou começar a monitorar os preços para você.
```

Cancelamento da confirmação:

```text
Combinado. Não vou criar essa missão.
```

## Listagem e comandos de missão

```text
Você ainda não tem nenhuma missão registrada.
```

```text
📋 Suas missões:

{ícone} {título} — {status}
```

```text
{ícone} Quer {ativar|pausar|retomar|concluir|cancelar|expirar} a missão "{título}"?

1 — Sim
2 — Não

Você também pode responder "sim" ou "não".
```

```text
Encontrei mais de uma missão para {ação}:

1 — {título} — {status}
2 — {título} — {status}

Digite o número (ex.: "1") ou vários separados por vírgula (ex.: "1,3").
```

```text
Não entendi. Digite o número de uma ou mais missões da lista, separados por vírgula (ex.: "1" ou "1,3").
Você não tem nenhuma missão cancelável.
Opção inválida. Escolha uma opção da lista.
```

Resultados:

```text
{ícone} "{título}" agora está {status}.
✅ {número}. "{título}" — {status}.
⚠️ {número}. "{título}" — já estava {status}.
❌ {número}. "{título}" — não encontrada.
```

## Edição de missão

```text
✏️ Para editar lojas ou preço-alvo, use /editar_missao.

A edição é feita por um menu guiado.
Você não tem nenhuma missão pausada ou ativa para editar agora.
```

Seleção dinâmica:

```text
Você tem mais de uma missão pausada. Qual você quer editar?

1 — {título}

Digite o número.
```

```text
Você tem mais de uma missão ativa. Qual você quer pausar para editar?

1 — {título}

Digite o número.
```

```text
Não entendi. Digite só o número da missão.
```

Missão ativa:

```text
⏸️ A missão "{título}" está ativa.

Preciso pausá-la antes de editar.

1 — Pausar e continuar
2 — Cancelar

Você também pode responder "sim" ou "não".
```

Menu:

```text
✏️ O que você quer editar na missão "{título}"?

1 — Lojas
2 — Preço-alvo

Digite o número.
```

```text
Não entendi. Digite "1" para lojas ou "2" para preço-alvo.
```

```text
🏪 O que você quer fazer?

1 — Adicionar lojas
2 — Remover lojas

Digite o número.
```

```text
Não entendi. Digite "1" para adicionar ou "2" para remover lojas.
🏪 Lojas ainda não vinculadas à missão:
🏪 Lojas atualmente vinculadas à missão:
Digite os números separados por vírgula.
Não entendi essa opção.

Use apenas os números mostrados, separados por vírgula.
A missão já tem todas as lojas disponíveis vinculadas.
Essa missão só tem uma loja vinculada.

Não dá para remover a última loja, porque a missão precisa ter pelo menos uma.

Não dá para remover todas as lojas.

A missão precisa manter pelo menos uma loja vinculada.
Escolha menos opções.
```

Preço-alvo:

```text
🎯 Digite o novo preço-alvo em reais.

Exemplos:
300
300,50

Para remover o preço-alvo, envie 0.
Não entendi o valor. Digite um número (ex.: 300), ou 0 para remover o alvo.
```

Confirmação:

```text
✏️ Confirmar edição da missão?

Missão: "{título}"
🎯 Alvo: {anterior} → {novo}             [quando alterado]
🏪 Lojas: {anteriores} → {novas}         [quando alteradas]

A missão continua pausada depois da edição — use /retomar quando quiser voltar a coletar.

1 — Confirmar
2 — Cancelar

Você também pode responder "sim" ou "não".
```

Sucesso:

```text
✅ Missão atualizada!

🔎 Missão: "{título}"
🎯 Alvo: {preço}
```

ou:

```text
🎯 Alvo: removido — agora só acompanhando preços.
```

e, quando aplicável:

```text
🏪 Lojas: {lojas}

A missão continua pausada — use /retomar quando quiser voltar a coletar.
```

## Preferências

```text
🔔 Suas preferências de notificações

{✅|🔕} Quedas de preço: {ativadas|desativadas}
{✅|🔕} Preço-alvo atingido: {ativadas|desativadas}

Use um destes comandos:

• /preferencias — consultar
• /preferencias quedas ativar|desativar
• /preferencias alvo ativar|desativar
```

```text
Não entendi.
A ação deve ser ativar ou desativar.
A preferência deve ser quedas ou alvo.
{✅|🔕} {Quedas de preço|Preço-alvo atingido}: notificações {ativadas|desativadas}.
```

Limite do webhook:

```text
Muitas mensagens em pouco tempo. Aguarde um minuto e tente novamente.
```

## Alertas e ofertas

### Queda de preço

```text
📉 QUEDA DE PREÇO

{produto}

🏪 {loja}
💰 {preço atual} (antes: {preço anterior})
🔎 Missão: {missão}

🔗 Ver anúncio
{url}
```

### Preço-alvo

```text
🔥 PREÇO ENCONTRADO

{produto}

🏪 {loja}
💰 {preço atual}
🎯 Alvo: {preço-alvo}
🔎 Missão: {missão}

🔗 Ver anúncio
{url}
```

### Pré-lista

```text
🧾 MELHORES OFERTAS ENCONTRADAS ATÉ AGORA

Missão: {missão}

Das lojas que você selecionou, essas são as melhores ofertas encontradas até agora:

🏪 {loja}
{produto}
💰 {preço}
🔗 Ver anúncio
{url}

⚠️ Valor sem frete. O frete será consultado na loja.

Ainda estou buscando nas outras lojas.
Se aparecer algo melhor, eu te aviso.
```

### Correção ou primeira oferta

```text
✏️ CORREÇÃO DA PRÉ-LISTA

Missão: {missão}

Uma das lojas que ainda estava buscando encontrou um preço melhor do que o mostrado antes:
```

ou:

```text
🧾 PRIMEIRA OFERTA RELEVANTE ENCONTRADA

Missão: {missão}

Ainda não tínhamos encontrado uma oferta relevante para essa missão.

Agora encontramos esta:
```

seguido de loja, produto, preço, link e aviso de frete no mesmo formato da pré-lista.

## Privacidade

```text
🔒 Privacidade

Usamos apenas os dados necessários para identificar sua conta, manter suas missões, autenticar seu acesso e enviar alertas.

Os textos dos seus pedidos podem ser enviados ao provedor de IA habilitado, e consultas de produtos podem ser feitas nas lojas selecionadas.

Evite enviar dados pessoais que não sejam necessários.

O responsável pela instância pode remover identificadores diretos e desativar a conta. Históricos técnicos necessários à integridade podem permanecer ligados apenas a um UUID interno pseudônimo; isso não é uma garantia de anonimização irreversível. Consulte docs/PRIVACY.md no projeto para os detalhes.
```

## Fontes autoritativas

- `backend/app/telegram/router.py`
- `backend/app/telegram/confirmation.py`
- `backend/app/telegram/preferences.py`
- `backend/app/telegram/notifications.py`
- `backend/app/users/registration.py`
- `backend/app/authentication/passwords.py`
- `backend/app/authentication/service.py`
- `backend/app/privacy/notice.py`
