# Operações de serviços independentes do runtime

O contrato administrativo usa apenas serviço lógico e operação fechada:
`status`, `start` e `restart`. UI e API não conhecem containers.

No runtime atual, `ControllerServiceOps` chama o `ops_controller` em rede
privada com assinatura; somente ele alcança o socket-proxy Docker. O controller
resolve os dois serviços por labels fixas e recusa qualquer outro nome.

Quando a instalação migrar de Docker para serviços diretos no servidor, será
criado um `WindowsServiceOpsAdapter` (ou supervisor equivalente) com o mesmo
contrato. Endpoints, permissões, auditoria e React não serão reconstruídos.
