# Log de incidentes de segurança

Este arquivo registra eventos de segurança relacionados ao ambiente de desenvolvimento.
Não contém credenciais, nomes de usuário completos nem conteúdo de quarentena.

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
