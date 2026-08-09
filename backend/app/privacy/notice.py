"""Aviso curto de privacidade exibido diretamente pelo Telegram."""

from typing import Final

PRIVACY_COMMAND: Final = "/privacidade"


def privacy_notice() -> str:
    """Explica o comportamento real sem prometer anonimização irreversível."""
    return (
        "Privacidade: usamos os dados mínimos necessários para identificar sua "
        "conta, manter missões, autenticar o acesso e enviar alertas. Textos de "
        "pedidos podem ser enviados ao provedor de IA habilitado e consultas de "
        "produto às lojas selecionadas. Não envie dados pessoais desnecessários.\n\n"
        "O responsável pela instância pode remover identificadores diretos e "
        "desativar a conta. Históricos técnicos necessários à integridade podem "
        "permanecer ligados apenas a um UUID interno pseudônimo; isso não é uma "
        "garantia de anonimização irreversível. Consulte docs/PRIVACY.md no "
        "projeto para os detalhes."
    )
