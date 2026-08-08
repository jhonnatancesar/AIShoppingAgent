# TASK-046 — Adicionar autenticação

Status: Concluída

## Objetivo

Autenticar minimamente a pessoa no canal Telegram atual do MVP, sem antecipar
login por senha, autorização ou gestão de segredos.

## Escopo

- manter a autenticação do transporte pelo segredo do webhook e comparação
  em tempo constante;
- confiar em `message.from.id` somente depois de autenticar o transporte;
- aceitar operações de usuário somente em chat privado quando
  `chat.id == message.from.id`;
- resolver/provisionar o `User` somente depois dessas validações;
- bloquear `is_active=false` antes de IA, missões, cadastro, preferências ou
  memorização do destino;
- recusas da identidade retornam `204` ao Telegram, não geram retry e registram
  apenas motivo fechado, sem IDs ou payload;
- nenhuma identidade pode ser aceita de texto ou payload de domínio.

## Fora do escopo

- senha, hashing, login HTTP, JWT, sessão, MFA e recuperação (TASK-061);
- permissões por papel (TASK-047);
- ciclo de vida/rotação de segredos (TASK-048);
- rate limit, replay protection e resiliência (TASK-049);
- migrations ou novas credenciais.

## Critério de aceite

Testes automatizados e validação real devem cobrir segredo válido/inválido,
primeiro acesso, usuário ativo/inativo, chat privado, grupo, supergrupo, canal,
divergência de identidade, ausência de efeitos colaterais, logs sanitizados,
PostgreSQL 18, API Docker e mensagem privada do Telegram reais. Executar o
pipeline completo e o workflow Git oficial.

## Resultado

- criada `app.telegram.authentication`, com comparação em tempo constante do
  segredo e resultado explícito para identidade aceita/recusada;
- grupo, supergrupo, canal e divergência entre chat e remetente são recusados
  antes de consultar ou criar `User`;
- conta inativa é recusada antes de IA, domínio, resposta e atualização do
  destino privado;
- recusas de identidade retornam `204` e logam somente o motivo fechado;
- nenhuma migration ou dependência foi adicionada.

## Validação

- pipeline completo em Python 3.14.6: 495 testes, 92,27% de cobertura, Ruff,
  grafo Alembic e Docker Compose aprovados;
- PostgreSQL 18 real: primeiro acesso criou uma única identidade, nova resolução
  foi idempotente, inativo foi recusado e entradas inválidas não alteraram o
  banco; a transação de validação foi revertida;
- API Docker real: segredo inválido retornou `401`; grupo e identidade
  divergente retornaram `204`; contagem de usuários permaneceu igual e canários
  não apareceram nos logs;
- Telegram real: mensagem privada chegou ao webhook com `204`, conta ativa e
  destino privado válidos, sem erro ou atualização pendente na Bot API.

Próxima tarefa executável: TASK-047.

