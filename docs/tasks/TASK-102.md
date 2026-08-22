# TASK-102 — Painel DEV/ADMIN e operações do sistema

Status: **Implementada no DEV, aguardando revisão.**

## Entrega

- dashboard técnico com dados reais de PostgreSQL e `unknown`/`unavailable`
  quando não existe prova de saúde;
- administração de usuários, papéis, ciclo de vida e missões, sempre por
  WebSession ADMIN/DEV, CSRF e auditoria append-only;
- remoção por tombstone: elimina credenciais, sessões e vínculos pessoais sem
  apagar missões, ofertas, observações ou histórico;
- providers habilitáveis e coleta controlada por agenda;
- operações `status/start/restart` sobre nomes lógicos allowlisted;
- estrutura de API keys persistível, mas emissão e autenticação desabilitadas.

## Segurança e runtime

A API e a UI dependem somente do contrato `ServiceOps` e dos nomes
`collection_worker`/`telegram_notifier`. O runtime atual usa um
`ops_controller` interno com `DockerOpsAdapter` explícito e socket-proxy
restrito. A API principal nunca recebe Docker socket, container ID ou comando.
A troca futura por `WindowsServiceOpsAdapter` altera somente o adapter.

## Fora de escopo

Terminal Web, shell arbitrário, editor SQL, health inventado, autenticação por
API key, deploy e acesso à produção.
