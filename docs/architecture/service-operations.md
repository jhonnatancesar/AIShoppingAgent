# Operações de serviços independentes do runtime

O contrato administrativo usa apenas serviço lógico e operação fechada:
`status`, `start` e `restart`. UI e API não conhecem containers nem Windows.

`ControllerServiceOps` sempre chama o `ops_controller` (Docker) em rede
privada com assinatura HMAC+timestamp+nonce; endpoints, permissões, auditoria
e React não mudam com o runtime de cada serviço por trás.

## Dois runtimes, um único controller

`ops_controller.py` despacha por `LogicalService`, cada um com seu próprio
adapter -- sem shell genérico, sem comando arbitrário:

- `telegram_notifier` -> `DockerOpsAdapter` (inalterado): resolve o container
  por label fixa (`com.docker.compose.service=...`) via o socket-proxy Docker,
  único ponto que conhece Docker.
- `collection_worker` -> `WindowsOpsAgentAdapter` (TASK-109, fechamento da
  migração): o worker roda nativo no Windows, fora do Docker (ver
  `docs/architecture/windows-collection-worker.md`). Este adapter é o único
  ponto do lado Docker que fala com o Windows Ops Agent -- HTTP loopback do
  host (`http://host.docker.internal:8021`, configurável por
  `WINDOWS_OPS_AGENT_URL`), request assinada com o mesmo esquema
  HMAC+timestamp+nonce (segredo próprio, `WINDOWS_OPS_AGENT_SECRET_FILE`,
  precisa ser copiado manualmente do valor gerado pelo próprio Ops Agent em
  `C:\ProgramData\AIShoppingAgent\secrets\ops-agent-secret` -- os dois lados
  não sincronizam sozinhos). Só três rotas fixas existem do lado do Ops Agent
  (`/v1/collection_worker/status|start|restart`); `status`/`start`/`restart`
  do contrato administrativo mapeiam 1:1, sem parâmetro livre.

O estado nativo (`Get-ScheduledTask` da task `AIShoppingAgent-CollectionWorker`)
é traduzido para o mesmo vocabulário que o Docker já usava
(`running`/`stopped`/`unavailable`/`unknown`), para que UI/API/auditoria
continuem agnósticas a qual runtime está por trás de cada serviço lógico.
