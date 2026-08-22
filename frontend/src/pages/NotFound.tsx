import { Link } from 'react-router-dom'
import { EmptyState } from '@/components/StatePanel'
import { Button } from '@/components/ui/button'

export function NotFound() {
  return (
    <EmptyState title="Página não encontrada" description="O endereço pode ter mudado ou não está disponível para a sua conta." action={<Button asChild><Link to="/app">Voltar para o início</Link></Button>} />
  )
}
