import { searchApi } from '../../api/search'
import { SearchHistoryList } from './SearchHistoryList'

/** "Todas as pesquisas" (Frente 5, DEV) -- única tela do produto que
 * lista pesquisas de TODOS os usuários (`GET /product-search/history/all`,
 * `Permission.DEV_PANEL_ACCESS`). Mostra `user_id` de propósito: é
 * ferramenta de apoio interno, não a view comunitária anônima de
 * `AppHome`. */
export function AllSearchesPage() {
  return (
    <SearchHistoryList
      fetchPage={(limit, offset) => searchApi.historyAll(limit, offset)}
      showOwner
      emptyDescription="Nenhuma pesquisa foi registrada por nenhum usuário ainda."
    />
  )
}
