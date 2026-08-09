# ADR-014 — Operação privada e recuperação manual validada

## Contexto

A V1 precisa de um procedimento reproduzível para um único Ubuntu Server sem
transformar ferramentas administrativas em superfícies públicas nem confundir
backup manual com disaster recovery. O Compose anterior publicava algumas
portas em todas as interfaces por omissão e a documentação não separava
rollback de aplicação de rollback de schema.

## Decisão

- API, PostgreSQL, Prometheus, Jaeger e health do Collector bindam no loopback
  por padrão; métricas do worker continuam somente na rede interna;
- acesso administrativo remoto usa túnel SSH ou canal privado equivalente;
- backup PostgreSQL é manual, armazenado com permissão restrita e só é tratado
  como validado depois de restauração em banco limpo e conferência dos dados;
- esse procedimento não é chamado de disaster recovery completo;
- rollback de código só ocorre com compatibilidade comprovada com o schema
  atual;
- downgrade Alembic destrutivo nunca é automático e incompatibilidade exige
  intervenção manual;
- cloudflared permanece somente para desenvolvimento/validação; produção futura
  exige domínio, TLS e endpoint HTTPS estável.

## Consequências

- defaults locais ficam mais seguros sem impedir acesso por localhost;
- operação remota exige um canal administrativo explícito;
- o operador recebe recuperação básica testável, mas ainda precisa definir
  RPO/RTO, cópias externas, automação e plano completo de desastre fora da V1;
- a TASK-051 não remove os bloqueios de scheduler, testes permanentes ou release.
