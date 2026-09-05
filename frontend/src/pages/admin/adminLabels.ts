const ADMIN_STATUS_LABELS: Record<string, string> = {
  healthy: 'Saudável',
  running: 'Em execução',
  succeeded: 'Concluída',
  active: 'Ativo',
  failed: 'Falhou',
  blocked: 'Bloqueado',
  inactive: 'Inativo',
  deleted: 'Removido',
  paused: 'Pausado',
  unavailable: 'Indisponível',
  not_configured: 'Não configurado',
}

export function adminStatusLabel(value: string) {
  return ADMIN_STATUS_LABELS[value] ?? value.replaceAll('_', ' ')
}
