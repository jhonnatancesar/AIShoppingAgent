# Instruções documentais para agentes

Este arquivo complementa o AGENTS.md na raiz. Em caso de conflito, as instruções mais restritivas prevalecem.

Antes de iniciar qualquer TASK, leia CLAUDE.md, todos os documentos deste diretório, ROADMAP.md e o arquivo específico em docs/tasks/. Execute apenas o escopo aprovado, atualize PROJECT_CONTEXT.md e CHANGELOG.md e não implemente funcionalidades futuras.

Antes da implementação, faça o preflight dos recursos reais exigidos pela TASK. Se faltarem chaves, contas, permissões, serviços ou infraestrutura que dependam do usuário, solicite-os antecipadamente e oriente a configuração segura fora do Git e do chat. Não substitua uma integração real previsível por uma implementação genérica ou apenas mockada.

As decisões aceitas ficam em adr/; propostas em evolução ficam em rfc/. A memória permanente do projeto é composta por CLAUDE.md e PROJECT_CONTEXT.md.
