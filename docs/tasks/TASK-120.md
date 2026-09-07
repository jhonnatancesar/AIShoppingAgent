# TASK-120 — Formatar datas do Histórico de missão para pt-BR/Brasília

Status: **Concluída e publicada em `origin/main`** (commit `a06397e`,
05/09/2026 -- `fix(web): format dates in pt-BR timezone`, cita esta TASK
diretamente no código). Novo helper centralizado
`frontend/src/lib/formatDateTime.ts` (`Intl.DateTimeFormat` pt-BR,
`timeZone: "America/Sao_Paulo"` explícito) já é usado em
`MissionDetailPage.tsx`/`transition.transitioned_at`, exatamente o ponto
descrito abaixo. Cabeçalho corrigido nesta auditoria de saneamento de
documentação (2026-09-07); o problema original permanece registrado
abaixo para contexto.

## Problema

Em `frontend/src/pages/missions/MissionDetailPage.tsx`, a seção "Histórico"
(dentro de `MissionDetailView`, no `map` de `mission.transitions`) renderiza
`{transition.transitioned_at}` diretamente. Esse campo vem do backend em ISO
8601 UTC (ex. `"2026-09-05T01:55:03.057728+00:00"`) e aparece cru na tela, sem
nenhuma formatação — ex.: `Ativa → Pausada (2026-09-05T01:55:03.057728+00:00)`.

## Objetivo

Formatar esse timestamp no padrão brasileiro, convertido para o fuso horário
de Brasília (`America/Sao_Paulo`), consistente com o resto do app.

Exemplo de abordagem (ajustar conforme padrão já estabelecido no restante do
código, ver abaixo):

```ts
new Intl.DateTimeFormat('pt-BR', {
  timeZone: 'America/Sao_Paulo',
  dateStyle: 'short',
  timeStyle: 'short',
}).format(new Date(transition.transitioned_at))
```

## Consistência com o resto do app

Outros pontos do frontend já usam `toLocaleString('pt-BR')` /
`toLocaleDateString('pt-BR')` para datas (ex.: `AccountPage.tsx`, seção
"Conta", `new Date(account.created_at).toLocaleDateString('pt-BR')`) —
**auditar se esses pontos já fixam `timeZone: 'America/Sao_Paulo'`
explicitamente**. Se não fixarem, o formato aparenta estar certo mas o fuso
depende do relógio local do navegador (não necessariamente Brasília) — nesse
caso, avaliar corrigir todos os pontos juntos para fuso explícito e
consistente, não só o Histórico de missão, para não deixar uma inconsistência
nova entre telas.

## Escopo

Só formatação de exibição de data/hora já existente. Não mudar nenhum outro
comportamento da tela, não mudar o formato que o backend envia
(`transitioned_at` continua ISO 8601 UTC no payload — a conversão é só na
apresentação).

## Fora de escopo

Qualquer mudança de contrato de API, qualquer mudança de lógica de negócio,
qualquer redesign visual além da formatação da data em si.

## Critério de validação futuro

Validar visualmente que o Histórico de uma missão real mostra a data/hora
certa, comparando com o valor UTC bruto que o backend retorna (considerando o
offset de Brasília, `-03:00` fora do horário de verão — o Brasil não usa
horário de verão desde 2019, então o offset é sempre `-03:00`).
