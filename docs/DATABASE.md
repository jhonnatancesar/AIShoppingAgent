# Banco de dados

PostgreSQL é o banco previsto. O modelo deverá conter entidades para usuários, missões, produtos, ofertas, coletas, observações de preço, eventos e trilhas de auditoria.

Cada observação de preço será imutável do ponto de vista histórico: uma nova coleta adiciona um registro em vez de substituir dados anteriores. O desenho físico e as migrações pertencem a tarefas futuras.

O modelo de missões da TASK-010 deverá incorporar os estados, transições e requisitos de histórico definidos em `docs/MISSION_SYSTEM.md`, mantendo o estado atual e um registro imutável de cada transição.
