"""Aviso curto de privacidade exibido diretamente pelo Telegram."""

from typing import Final

PRIVACY_COMMAND: Final = "/privacidade"


def privacy_notice() -> str:
    """Explica o comportamento real sem prometer anonimização irreversível."""
    return (
        "🔒 Privacidade\n\n"
        "Usamos apenas os dados necessários para identificar sua conta, manter "
        "suas missões, autenticar seu acesso e enviar alertas.\n\n"
        "Os textos dos seus pedidos podem ser enviados ao provedor de IA "
        "habilitado, e consultas de produtos podem ser feitas nas lojas "
        "selecionadas.\n\n"
        "Evite enviar dados pessoais que não sejam necessários.\n\n"
        "O responsável pela instância pode remover identificadores diretos e "
        "desativar sua conta. Alguns históricos técnicos necessários à integridade "
        "do sistema podem permanecer vinculados apenas a um UUID interno "
        "pseudônimo.\n\n"
        "Isso não representa garantia de anonimização irreversível.\n\n"
        "Para mais detalhes, consulte docs/PRIVACY.md no projeto."
    )
