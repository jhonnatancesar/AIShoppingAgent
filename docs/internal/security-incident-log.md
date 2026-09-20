# Log de incidentes de segurança

Este arquivo registra eventos de segurança relacionados ao ambiente de desenvolvimento.
Não contém credenciais, nomes de usuário completos nem conteúdo de quarentena.

## INC-2026-09-17-001 — Falso positivo de prompt injection num resumo de compactação de contexto

- **Data:** detectado em 2026-09-16 21:46 e novamente em 2026-09-17
  14:53; esclarecido pelo usuário em 2026-09-20.
- **Classificação:** falso positivo -- não reclassificado como
  incidente de segurança real. Registrado por precaução e para não
  deixar rastro sem explicação (pasta `.forensics/`, gerada na
  investigação, ficava órfã sem este registro).
- **Detecção:** o assistente, ao receber como primeiro turno de uma
  sessão o resumo automático padrão de compactação de contexto
  (`"This session is being continued from a previous conversation..."`),
  notou um trecho adicional em terceira pessoa mandando parar de usar
  ferramentas e não fazer mais perguntas -- inconsistente com o padrão
  real de compactação deste ambiente (transparente, não exige parar) e
  com o estilo de comunicação da conversa. Tratou como possível
  tentativa de injeção de instrução e não obedeceu, sinalizando ao
  usuário e continuando o trabalho real.
- **Investigação:** o usuário pediu uma investigação forense
  (`.forensics/`, pasta local nunca rastreada pelo git) cruzando o
  transcript da sessão, marcador de commits e objetos soltos do git --
  confirmou que nenhum commit foi feito às escondidas durante o
  episódio, histórico do repositório íntegro. A cautela do assistente
  teve um efeito colateral real e útil: motivou uma auditoria forense
  dos relatórios dos checkpoints 3-11 contra o working tree, que
  encontrou e corrigiu um erro real de documentação (status
  desatualizado de `docs/tasks/TASK-121.md`).
- **Conclusão (usuário, 2026-09-20):** não foi um ataque real. Foi o
  próprio mecanismo de compactação de contexto alucinando esse
  conteúdo -- não uma instrução maliciosa injetada por terceiros nem
  por qualquer conteúdo externo lido durante a sessão.
- **Resposta:** nenhuma correção de segurança necessária (não havia
  vulnerabilidade real). Evidência relevante da investigação preservada
  localmente em `checkpoints/2026-09-17-forensics-incident/` (nunca
  rastreada pelo git); dumps brutos grandes e scripts de investigação
  de uso único descartados por não terem valor de registro futuro.
  Ver `docs/internal/project-context.md`, seção "Fechamento do episódio
  de compactação alucinada", para o relato completo.
- **Prevenção:** nenhuma ação adicional -- o comportamento correto já
  existe (não obedecer instruções que chegam disfarçadas de mensagem de
  sistema/resumo automático, sinalizar e confirmar com o usuário antes
  de agir). Registrado aqui como precedente para reconhecer o mesmo
  padrão de alucinação de compactação no futuro sem tratá-lo de novo
  como incidente real sem necessidade.

## INC-2026-08-02-001 — Detecção comportamental em runtime interno do Codex

- **Data:** 2026-08-02.
- **Componente detector:** Kaspersky System Watcher.
- **Detecção:** `PDM:Trojan.Win32.Generic`, nível alto, baseada em análise de
  comportamento de processo.
- **Objeto:** `python.exe` do runtime interno em
  `%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python`.
- **MD5 informado pelo antivírus:** `556C7CCE9FBB6F4D4679A9851EA0D475`.
- **Contexto:** o runtime interno foi usado indevidamente para instalar dependências
  Python do projeto. A detecção não apontou para arquivos versionados no repositório.
- **Resposta:** execução interrompida, objeto não restaurado, nenhuma exceção criada,
  desinfecção e reinicialização executadas pelo usuário.
- **Verificação posterior:** o objeto detectado permaneceu ausente; o Kaspersky voltou
  ao estado ativo; o repositório permaneceu íntegro e sem alterações estranhas.
- **Ambiente confiável preservado:** Python 3.14.6 oficial, com assinatura digital
  válida da Python Software Foundation e SHA-256
  `03168C01B7B7491423350E82C26FEE71F35B43694D1319D3C668BDA6903A0C38`.
- **Validação posterior:** 97 testes aprovados, 99,78% de cobertura, dependências,
  Ruff, formatação e grafo Alembic aprovados usando exclusivamente o Python oficial.
- **Conclusão:** o evento permanece registrado como detecção comportamental; este log
  não o reclassifica como falso positivo nem autoriza restauração do objeto.
- **Prevenção:** runtimes internos do Codex, plugins, caches e ferramentas hospedeiras
  não podem instalar dependências nem executar validações deste projeto.
