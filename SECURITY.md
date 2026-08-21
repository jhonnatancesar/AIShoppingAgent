# Política de segurança

Este é um projeto pessoal, mantido por uma única pessoa, em repositório
privado.

## Reportando uma vulnerabilidade

Se você tem acesso a este repositório e encontrar uma vulnerabilidade de
segurança (exposição de credencial, falha de autenticação/autorização,
injeção, ou qualquer forma de acesso indevido a dados), reporte diretamente
ao mantenedor, fora de qualquer canal público — não abra uma issue pública
descrevendo o problema em detalhe.

## Escopo

Cobre o código deste repositório (`backend/`, scripts de operação,
`compose.yaml`) e sua configuração de implantação. Não cobre políticas de
segurança de terceiros integrados (Telegram, Gemini, Groq, OpenRouter,
Pichau, Terabyte, Amazon, KaBuM!).

## Versões suportadas

Somente a versão mais recente publicada (ver [Releases](docs/releases/index.md))
recebe correções de segurança. Não há suporte a versões antigas.

## Vazamento de credencial

Se uma credencial (senha do banco, chave de IA, token do bot, segredo do
webhook) foi exposta — em commit, log, mensagem ou qualquer outro canal —
trate como incidente: revogue/rotacione imediatamente no provedor
correspondente. O procedimento de rotação está em
[Secrets](docs/installation/secrets.md); o registro de incidentes
(sanitizado, sem o valor exposto) fica em
[Log de incidentes de segurança](docs/internal/security-incident-log.md).
