# Backup e restauração

Procedimento manual — não há automação de backup no projeto. O backup
contém dados potencialmente pessoais: crie um diretório privado e nunca
inclua secret no nome, comando ou saída.

## Gerar um backup

```powershell
New-Item -ItemType Directory -Force backups | Out-Null
$BACKUP_FILE = "backups/postgres-$(Get-Date -AsUTC -Format 'yyyyMMddTHHmmssZ').dump"
docker compose exec -T database sh -c 'pg_dump --format=custom --no-owner --no-privileges --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' > $BACKUP_FILE
docker compose exec -T database pg_restore --list < $BACKUP_FILE | Out-Null
Get-Item $BACKUP_FILE | Select-Object Name, Length
```

Resultado esperado: arquivo não vazio em `backups/`. Guarde-o em local
controlado — cópia externa exige criptografia, retenção e controle de
acesso definidos pelo operador (fora do escopo da V1). Restrinja o acesso
ao arquivo e ao diretório `backups/` ao operador responsável.

## Teste de restauração (sempre em banco descartável, nunca sobre o ativo)

Backup não testado não é considerado validado.

```powershell
docker compose exec -T database sh -c 'dropdb --if-exists --username="$POSTGRES_USER" aishoppingagent_restore_validation'
docker compose exec -T database sh -c 'createdb --username="$POSTGRES_USER" aishoppingagent_restore_validation'
docker compose exec -T database sh -c 'pg_restore --exit-on-error --no-owner --no-privileges --username="$POSTGRES_USER" --dbname=aishoppingagent_restore_validation' < $BACKUP_FILE
docker compose exec -T database sh -c 'psql --username="$POSTGRES_USER" --dbname=aishoppingagent_restore_validation --set=ON_ERROR_STOP=1 --command="SELECT version_num FROM alembic_version;"'
```

Compare no banco original e no restaurado as contagens das tabelas
críticas e confirme a leitura de um registro sintético conhecido. Não
imprima nomes, e-mails, Telegram IDs, tokens, hashes ou textos reais
durante a conferência. Depois de validar, e só quando tiver certeza de que
esse é o banco descartável:

```powershell
docker compose exec -T database sh -c 'dropdb --username="$POSTGRES_USER" aishoppingagent_restore_validation'
```

## Restaurar sobre o banco ativo (recuperação de desastre)

Use apenas quando o banco de produção realmente precisa ser substituído
pelo conteúdo do backup — isso descarta qualquer dado gravado depois do
backup escolhido.

1. Pare os serviços que escrevem no banco (mantenha `database` rodando):

   ```powershell
   docker compose stop api collection_worker telegram_notifier
   ```

2. Restaure sobre o banco real (⚠️ substitui os dados existentes):

   ```powershell
   docker compose exec -T database sh -c 'pg_restore --exit-on-error --clean --if-exists --no-owner --no-privileges --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' < $BACKUP_FILE
   ```

3. Confirme a revisão Alembic restaurada e aplique migrations pendentes se
   o backup for de uma versão anterior do código — veja
   [Migrations](../database/migrations.md).

4. Suba os serviços de novo e rode a validação completa:

   ```powershell
   docker compose up -d
   ```

   Veja [Windows Server → Validação inicial](../installation/windows-server.md#10-validação-inicial).

Este processo comprova backup operacional manual e restauração naquele
teste. Ele não oferece disaster recovery completo, replicação,
point-in-time recovery, automação, cópia off-site nem garantias formais de
RPO/RTO.

## O que é persistido

Só o volume nomeado do PostgreSQL (`aishoppingagent_postgres_data`) é dado
de negócio. Métricas do Prometheus (`aishoppingagent_prometheus_data`) são
úteis para histórico, mas perdê-las não afeta a aplicação. Traces do
Jaeger são voláteis por design e não sobrevivem a um restart do container
`jaeger`.
