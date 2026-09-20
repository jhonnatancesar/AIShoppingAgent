import { searchApi } from '../../api/search'
import { SearchHistoryList } from './SearchHistoryList'

export function MySearchesPage() {
  return (
    <SearchHistoryList
      fetchPage={(limit, offset) => searchApi.historyMine(limit, offset)}
      showOwner={false}
      emptyDescription="Suas pesquisas em /app/search aparecem aqui assim que você fizer uma."
    />
  )
}
