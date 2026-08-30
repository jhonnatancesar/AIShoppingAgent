import { useState } from 'react'

/**
 * Subtask 4 (auditoria GG Oferta, revisão): a imagem canônica de um
 * Product é `set-once` na coleta e pode ficar indisponível no futuro
 * (URL expirada, CDN mudou) sem nenhum reparo automático -- por isso a
 * apresentação nunca depende só dela. Tenta cada URL não-nula da lista,
 * na ordem, e avança para a próxima só quando a atual falha de verdade
 * (`onError`); sem URL restante, `src` fica `undefined` e quem chama
 * decide o fallback visual (ícone, texto).
 */
export function useImageFallbackChain(candidates: (string | null | undefined)[]) {
  const urls = candidates.filter((url): url is string => Boolean(url))
  const [attempt, setAttempt] = useState(0)
  return {
    src: urls[attempt],
    onError: () => setAttempt((current) => current + 1),
  }
}
