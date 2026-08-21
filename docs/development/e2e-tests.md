# Testes ponta a ponta

A TASK-053 separa regressão reproduzível de validação externa. Nenhuma das
duas insere observações/eventos nem chama avaliador/notifier diretamente.

## Reproduzível

Execute no Windows:

```powershell
.\scripts\check-e2e.cmd
```

Ou, no Windows/Ubuntu Server com o Python oficial:

```bash
python scripts/run_e2e_tests.py
```

O runner reutiliza o guard fail-closed da TASK-052, cria PostgreSQL 18.4,
volume, banco-template e banco por teste com identidades aleatórias, executa
migrations reais e remove nominalmente todos os recursos. O teste usa o
webhook HTTP e os loops reais dos workers. Somente IA, marketplace e Telegram
são fronteiras controladas, para que a Internet não determine o pipeline
cotidiano.

O cenário de onboarding percorre `/cadastro`, seleção numerada, criação de
senha e `/entrar` pela API real. Em seguida executa o notifier e confirma no
chat controlado as mensagens de senha/login, o aviso prévio e a expiração. Um
novo ciclo/restart precisa manter a contagem, provando que os marcadores e o
consumo terminal não duplicam o histórico.

## Externo

O modo externo é manual, explícito e obrigatório para encerrar a TASK-053:

1. subir um projeto Compose e volume descartáveis;
2. expor apenas a API por HTTPS temporário com cloudflared;
3. guardar e substituir temporariamente o webhook do bot;
4. autenticar pelo fluxo público real;
5. criar e confirmar pelo Telegram uma missão com as quatro fontes, consulta
   de teste e alvo BRL deliberadamente alto;
6. aguardar os workers sem chamar a cadeia interna;
7. classificar somente pelas evidências persistidas:

```bash
python -m scripts.validate_external_e2e --mission-title "<titulo exato>"
```

Resultados finais:

- `PASS`: evento de alvo e consumo Telegram terminal bem-sucedido;
- `FAIL_INTERNO`: evidência elegível chegou, mas uma garantia interna falhou;
- `BLOCKED_EXTERNAL`: terceiros impediram evidência elegível ou entrega;
- `PENDING`: somente estado intermediário; nunca encerra a validação.

Na V1 (`DEC-045`), evidência elegível exige preço real do produto
(`amount`), disponibilidade válida e moeda compatível — **frete não faz
parte do critério de elegibilidade externa**, porque o monitoramento da V1
compara `amount`, nunca `total_amount`. O alvo alto torna o alerta
previsível sem fabricar preço. Preço, moeda e disponibilidade continuam
vindo do marketplace. CAPTCHA, 403, mudança de markup ou indisponibilidade
externa nunca são convertidos em sucesso. Ao terminar, o webhook anterior
deve ser restaurado e o projeto/volume descartáveis removidos pelo nome
exato.

Frete continua nunca sendo fabricado nem tratado como zero/grátis quando
desconhecido — só deixou de ser exigido para o monitoramento de preço da
V1. Frete/parcelamento precisos e autenticados ficam para a V1.2 (somente
ADMIN/DEV, `docs/internal/v1.2-scope.md`) e, futuramente, para usuários comuns na V2
(`docs/internal/backlog.md`). A V1 não recebe credenciais de marketplace e o E2E não
amplia esse escopo.
