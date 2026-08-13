# TASK-083 — Confiabilidade da canonicalização de produto: especificidade, confiança e consulta externa quando necessário

Status: **Auditada nesta rodada (2026-08-13, Etapa 4 do planejamento
pós-v1.0.6)** — comportamento atual documentado, opções levantadas sem
escolha de tecnologia; não implementada.

Dependência: estende `TASK-075`/`TASK-074` (`IntentInterpreter`,
canonicalização, campo `model`) sem reabri-las. **TASK-082 (Etapa 3)
depende parcialmente desta** — o gate interino `criteria.model is None`
proposto lá deve ser revisado para usar a classificação de
especificidade proposta aqui, quando ela existir.

Versão alvo: a definir pelo usuário.

## Problema (caso real relatado)

Entrada do usuário: `"9800X3D"`. Resposta observada da IA em uma
interação real: equivalente a `"Ryzen 9 9800X3D"`. **Isso é
tecnicamente incorreto** — o produto real é `AMD Ryzen 7 9800X3D`
(9800X3D pertence à linha Ryzen 7; 9950X3D/9900X3D é que são Ryzen 9).

**Achado relevante desta auditoria**: a própria validação da TASK-075
(2026-08-11, "Validação final") registrou, para o mesmo termo,
`"procura um 9800x3d"` → `search_query: "Processador AMD Ryzen 7
9800X3D"` — **família correta na época**. Ou seja, o mesmo modelo
recebeu respostas diferentes (uma correta, uma errada) em interações
diferentes com a IA. Isso não é um bug determinístico de código — é a
consequência esperada de uma canonicalização que depende inteiramente
do conhecimento treinado do modelo de IA, numa única chamada, sem
nenhuma verificação externa. Confirmado por auditoria de código: a IA
pode "chutar" com confiança aparente e errar, sem que o sistema tenha
qualquer forma de perceber.

## Auditoria do `IntentInterpreter` atual

`backend/app/intent/interpreter.py` (leitura completa):

- **Uma única chamada de IA** (`AIProviderManager.generate`, `purpose =
  "interpret_purchase_intent"`) por mensagem — sem chamada de
  verificação, sem grounding, sem busca externa.
- O prompt (`_SYSTEM_PROMPT`) instrui a IA a "montar uma descrição
  canônica completa, na ordem Tipo Marca Linha/Família Modelo" e a
  preencher `model` "quando houver um identificável com segurança" —
  mas **não há mecanismo algum para a IA expressar incerteza**: a saída
  é sempre um JSON fechado (`search_query`/`model` como string ou
  `null`), sem campo de confiança. "Identificável com segurança" é uma
  instrução textual no prompt, não uma garantia estrutural — o modelo
  pode preencher com um valor errado exatamente como se estivesse
  seguro.
- **Nenhum sinal de especificidade da busca existe hoje além de
  `model` presente/ausente** (binário) — não distingue "específica"
  de "parcialmente específica" de "genérica" de "ambígua", os quatro
  níveis que o usuário pediu para diferenciar.

## Auditoria do `AIProviderManager`/contratos — capacidade de consulta externa

`backend/app/ai_provider/contracts.py`: `AIRequest`/`AIResponse` são um
contrato agnóstico de **texto puro** (`messages: tuple[AIMessage, ...]`
→ `content: str`). **Não existe campo de `tools`, `grounding` ou busca**
em nenhum lugar do contrato — qualquer capacidade de consulta externa
exigiria estender esse contrato compartilhado (decisão arquitetural,
não implementação isolada).

Por provedor:

- **`gemini.py`** (`GeminiProvider.generate`): usa `google.genai`,
  `client.aio.models.generate_content(model=..., contents=..., config=...)`,
  `config` só define `system_instruction`. O SDK já importado
  (`google.genai.types`) **suporta nativamente grounding via Google
  Search** como uma `Tool` opcional em `GenerateContentConfig` — hoje
  **não usado**. É a opção de menor atrito técnico, porque a dependência
  já existe no projeto; ainda assim, exige decisão sobre custo/latência
  e não deve ser escolhida só por ser a mais fácil.
- **`groq.py`** (`GroqProvider.generate`): chamada HTTP direta
  (`httpx`) ao endpoint de chat completions da Groq, sem nenhuma
  ferramenta de busca configurada. O modelo hoje configurado
  (`llama-3.3-70b-versatile`) não tem busca embutida no plano atual do
  projeto. **Se a solução escolhida for grounding nativo do provedor
  (opção acima), ela cobre só o perfil que usa Gemini** — a cascata
  `ADMIN`/`DEV` (Gemini → Groq) ficaria com uma lacuna de cobertura no
  fallback Groq, ponto que qualquer decisão futura precisa endereçar
  explicitamente, não ignorar.
- **Nenhuma API de catálogo estruturado de fabricante já integrada** —
  confirmado por ausência: os únicos clientes HTTP externos do projeto
  são os quatro Store Providers (scraping das lojas) e os dois
  provedores de IA. Uma consulta a catálogo oficial (ex.: página de
  especificações da AMD/NVIDIA) seria uma integração nova, sem
  precedente no projeto hoje.

## Requisitos do usuário para esta TASK

1. Distinguir pelo menos 4 níveis de especificidade da busca:
   **específica** (`"9800X3D"`, `"RTX 5070 Ti"`), **parcialmente
   específica** (`"monitor Samsung 27"`), **genérica** (`"cadeira
   gamer"`, `"mouse"`), **ambígua** (mensagem cujo sentido não permite
   nem classificar com segurança).
2. Quando a mensagem for específica o bastante para exigir
   preenchimento de fabricante/linha/família/categoria/geração, mas a
   IA não tiver confiança suficiente, o sistema deve **consultar uma
   fonte antes de afirmar** — nunca completar por chute.
3. Não pesquisar externamente toda mensagem — só quando necessário.
4. Evitar custo e latência desnecessários; nunca adicionar uma segunda
   chamada de IA obrigatória para toda missão.
5. Preferir fonte oficial/forte; não confiar cegamente no primeiro
   resultado.
6. Cachear identidade já resolvida, se fizer sentido.
7. Manter fallback seguro quando a consulta externa falhar.

## Opções levantadas (nenhuma escolhida — decisão do usuário)

**A. Confiança auto-relatada + segunda chamada condicional.** O prompt
passa a pedir um indicador de confiança junto com `model`/`search_query`
(ex.: um campo fechado `"model_confidence": "alta" | "baixa" | null`).
Só quando `"baixa"`, o sistema aciona uma segunda consulta (grounding ou
outra fonte) — resolve o requisito 3/4 (não pesquisa toda mensagem), mas
depende da própria IA relatar corretamente sua incerteza, o que não é
garantido (o caso real relatado mostra a IA "confiante" e errada, não
necessariamente vai se autodeclarar insegura).

**B. Grounding nativo do provedor (Gemini Search) só como segunda etapa
verificadora.** Não substitui a chamada de canonicalização; só valida o
resultado já produzido quando algum critério determinístico achar
suspeito (ex.: nenhuma correspondência de padrão contra uma lista
conhecida de famílias por fabricante — ver Opção C). Cobertura só para
o perfil que efetivamente usa Gemini na chamada (não resolve a lacuna
do fallback Groq, apontada acima).

**C. Validação determinística leve, sem IA nem rede, como primeira
linha.** Uma tabela pequena e explícita de padrões conhecidos por
fabricante (ex.: regras de nomenclatura AMD Ryzen: sufixo `X3D` +
prefixo numérico define a linha; NVIDIA RTX: série + sufixo Ti/SUPER)
usada só para **detectar contradição** entre o que a IA respondeu e um
padrão determinístico conhecido — não para gerar a resposta. Barata,
sem latência de rede, sem custo de IA — mas cobre só famílias de
produto cadastradas manualmente na tabela (mesma limitação de
manutenção já discutida na TASK-075 para `product_type`); não ajuda em
categorias fora da tabela.

**D. Combinação A+C**: validação determinística (C) roda sempre
(grátis, sem rede); só aciona consulta externa (A/B) quando C encontra
contradição ou quando a IA já se autodeclara insegura. Reduz a
frequência de chamada externa ao mínimo necessário, mas tem o maior
custo de implementação/manutenção entre as opções.

Nenhuma das quatro é escolhida por esta TASK — **decisão explícita do
usuário antes de qualquer implementação**, incluindo se a resposta
correta é simplesmente não resolver isso agora (aceitar o risco
residual, já que o filtro determinístico da TASK-075 rejeita variantes
erradas no título quando o modelo *é* preenchido corretamente — o
problema é só quando o preenchimento em si já nasce errado).

## Cache de identidade — avaliação preliminar

Existe precedente próximo no projeto: `MissionOfferRelevance`/
`Product.display_name` já funcionam como cache de resultado de IA por
chave (TASK-063, mencionado na TASK-079). Uma tabela equivalente para
"identidade de modelo resolvida" (`model` normalizado → fabricante/
linha/categoria confirmados) é tecnicamente viável seguindo o mesmo
padrão — mas **decisão de implementação, não desta auditoria**: precisa
definir chave de cache, invalidação (um modelo pode ganhar uma variante
nova no mercado depois do cache gravado), e se vale a pena para o
volume real de modelos distintos pedidos.

## Conjunto de testes de regressão a criar (nenhum implementado ainda)

**Caso obrigatório** (da forma exata pedida): `"9800X3D"` →
`search_query` contendo `"AMD Ryzen 7 9800X3D"`, `model = "9800X3D"` —
**nunca** `"Ryzen 9 9800X3D"`.

Conjunto adicional, cobrindo as categorias pedidas (a definir os valores
exatos esperados quando a solução for escolhida — listados aqui como
categoria de caso, não como asserção pronta):

- **CPUs** com risco de confusão de linha/família: `9800X3D` (Ryzen 7,
  caso obrigatório), `9950X3D`/`9900X3D` (Ryzen 9, já validados pela
  TASK-075), `7600X` vs `7600` (com/sem sufixo `X`, famílias adjacentes).
- **GPUs**: `RTX 5070 Ti` vs `RTX 5070` vs `RTX 5070 Ti SUPER` (mesma
  tabela de sufixos já usada na TASK-075).
- **Periféricos**: modelo sem fabricante explícito (ex.: `"g pro 2"` →
  precisa resolver para Logitech sem o usuário mencionar a marca).
- **Modelos sem fabricante na mensagem**: mesmo caso acima, generalizado
  — quando o modelo sozinho já identifica o fabricante de forma
  inequívoca (ex.: nomenclatura exclusiva de uma marca) vs quando não
  identifica (ambíguo entre fabricantes, deve cair em confiança baixa
  ou `unknown`, nunca chutar).
- **Abreviações**: `"9800x3d"` minúsculo, sem espaço, variações de
  digitação já cobertas pela TASK-074/075.
- **Modelos ambíguos**: um código que pareça modelo mas não corresponda
  a nenhum produto real conhecido — deve resultar em confiança baixa ou
  `model: null`, nunca invenção.
- **Pesquisas genéricas**: `"cadeira gamer"`, `"mouse"` — `model: null`
  esperado, sem acionar nenhuma consulta externa (requisito 3).
- **Parcialmente específicas**: `"monitor Samsung 27"` — tem fabricante
  e uma característica (tamanho), mas não um SKU exato; classificação
  esperada como categoria própria (nem específica nem genérica),
  comportamento a definir junto da opção escolhida.

## Fora de escopo

- Reabrir o filtro determinístico de modelo/bundle ou a regra da Amazon
  (TASK-075) — o problema aqui é a **entrada** do filtro (o `model`
  vindo errado da IA), não o filtro em si.
- Escolher ou implementar a tecnologia de consulta externa — só
  auditoria e opções nesta TASK.
- Qualquer mudança em `classify_offer_relevance`/normalização de título
  de oferta (TASK-063) — assunto de canonicalização de *missão*, não de
  *oferta já coletada*.
- Resolver o número/gate de limitação de candidatos da TASK-082 — só
  registra a dependência.

## Critérios de aceite (quando a opção escolhida for implementada)

1. Caso obrigatório `9800X3D` → `AMD Ryzen 7 9800X3D` / `model =
   "9800X3D"` passa de forma determinística (não só "geralmente
   funciona") — critério objetivo: N execuções reais consecutivas sem
   nenhuma resposta incorreta, valor de N a definir com a opção
   escolhida.
2. Nenhuma chamada externa nova acontece para busca genérica (`"cadeira
   gamer"`) nem para busca já confiante (regressão da TASK-075
   continua passando sem chamada extra).
3. Latência adicional média por missão dentro de um teto aceitável a
   definir (evitar que toda `create_mission` fique perceptivelmente
   mais lenta).
4. Custo adicional de IA/consulta externa mensurável e proporcional —
   não uma segunda chamada obrigatória por missão.
5. Fallback seguro comprovado: consulta externa falhando (timeout,
   indisponibilidade) não trava nem derruba a criação da missão —
   degrada para o comportamento atual (aceitar a resposta da IA como
   está, ou `model: null`, a definir).
6. Pipeline oficial completo aprovado antes de qualquer commit.

## Impacto em banco/migration

Depende da opção escolhida — Opção A/B sem consulta externa cacheada:
nenhum. Se cache de identidade for adotado: nova tabela/coluna,
migration própria, política de retenção a definir — não decidido nesta
auditoria.
