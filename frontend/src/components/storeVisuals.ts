export type StoreCode = 'amazon' | 'kabum' | 'magalu' | 'mercadolivre' | 'pichau' | 'terabyte'

const STORE_VISUALS: Record<StoreCode, { name: string; logo: string; background: string; accent: string; wide?: boolean }> = {
  amazon: { name: 'Amazon', logo: '/store-logos/amazon.png', background: '#131921', accent: '#e47911', wide: true },
  kabum: { name: 'KaBuM!', logo: '/store-logos/kabum.png', background: '#07559b', accent: '#07559b' },
  magalu: { name: 'Magalu', logo: '/store-logos/magalu.png', background: '#0086ff', accent: '#0086ff' },
  mercadolivre: { name: 'Mercado Livre', logo: '/store-logos/mercado-livre.png', background: '#ffe600', accent: '#2d3277' },
  pichau: { name: 'Pichau', logo: '/store-logos/pichau.png', background: '#101114', accent: '#d71920', wide: true },
  terabyte: { name: 'Terabyte', logo: '/store-logos/terabyte.png', background: '#111827', accent: '#f05a28' },
}

export function storeCodeFrom(value: string): StoreCode | null {
  const normalized = value.toLocaleLowerCase('pt-BR').replaceAll(/[^a-z]/g, '')
  if (normalized.includes('mercadolivre') || normalized.includes('mercadolibre')) return 'mercadolivre'
  if (normalized.includes('amazon')) return 'amazon'
  if (normalized.includes('kabum')) return 'kabum'
  if (normalized.includes('magalu') || normalized.includes('magazineluiza')) return 'magalu'
  if (normalized.includes('pichau')) return 'pichau'
  if (normalized.includes('terabyte')) return 'terabyte'
  return null
}

export function getStoreVisual(value: string) {
  const code = storeCodeFrom(value)
  return code ? STORE_VISUALS[code] : null
}
