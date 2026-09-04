import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { createServer } from 'vite'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({ root, appType: 'custom', server: { middlewareMode: true } })
try {
  const { OfferCard } = await server.ssrLoadModule('/src/components/OfferCard.tsx')

  const baseOffer = {
    id: 'offer-1',
    title: 'Galaxy S24 Ultra',
    imageUrl: null,
    store: { name: 'Amazon' },
    price: { amount: '4599.00', totalAmount: '4619.00', currency: 'BRL' },
    condition: 'used',
    seller: { name: 'Amazon.com.br' },
    rating: { average: '4.8', reviewCount: 125 },
  }

  // 1) Ação interna (`to`) usa <Link>, texto "Ver detalhes".
  const internalHtml = renderToStaticMarkup(
    React.createElement(MemoryRouter, null, React.createElement(OfferCard, { offer: baseOffer, action: { label: 'Ver detalhes', to: '/app/offers/offer-1' } })),
  )
  assert.match(internalHtml, /R\$\s*4\.599,00/)
  assert.match(internalHtml, /Total R\$\s*4\.619,00/)
  assert.match(internalHtml, /Usado/)
  assert.match(internalHtml, /Vendido por Amazon\.com\.br/)
  assert.match(internalHtml, /href="\/app\/offers\/offer-1"/)
  assert.match(internalHtml, />Ver detalhes /)
  assert.doesNotMatch(internalHtml, /target="_blank"/)

  // 2) Ação externa (`href`) abre em nova aba, texto "Ver na loja".
  const externalHtml = renderToStaticMarkup(
    React.createElement(MemoryRouter, null, React.createElement(OfferCard, { offer: baseOffer, action: { label: 'Ver na loja', href: 'https://amazon.com.br/dp/example' } })),
  )
  assert.match(externalHtml, /href="https:\/\/amazon\.com\.br\/dp\/example"/)
  assert.match(externalHtml, /target="_blank"/)
  assert.match(externalHtml, />Ver na loja /)

  // 3) Sem observação comercial: mensagem honesta, nunca preço inventado.
  const noPriceOffer = { ...baseOffer, price: null, condition: null }
  const noPriceHtml = renderToStaticMarkup(
    React.createElement(MemoryRouter, null, React.createElement(OfferCard, { offer: noPriceOffer, action: { label: 'Ver detalhes', to: '/app/offers/offer-1' } })),
  )
  assert.match(noPriceHtml, /Preço ainda não coletado/)
  assert.doesNotMatch(noPriceHtml, /R\$/)

  // 4) `selected`/`onSelect` são genéricos (Subtask 13) -- card inteiro
  // clicável na área da imagem, com dica de seleção, sem o componente saber
  // que isso é "modo pesquisa/categoria genérica" (decisão do chamador).
  const selectableHtml = renderToStaticMarkup(
    React.createElement(MemoryRouter, null, React.createElement(OfferCard, {
      offer: baseOffer,
      action: { label: 'Ver na loja', href: 'https://amazon.com.br/dp/example' },
      selected: true,
      onSelect: () => undefined,
    })),
  )
  assert.match(selectableHtml, /Clique para selecionar esta oferta/)
  assert.match(selectableHtml, /ring-2 ring-primary/)

  // 5) Sem `rating`/`seller`, nada é inventado -- simplesmente omitido.
  const minimalOffer = { ...baseOffer, seller: null, rating: null }
  const minimalHtml = renderToStaticMarkup(
    React.createElement(MemoryRouter, null, React.createElement(OfferCard, { offer: minimalOffer, action: { label: 'Ver detalhes', to: '/app/offers/offer-1' } })),
  )
  assert.doesNotMatch(minimalHtml, /Vendido por/)
  assert.doesNotMatch(minimalHtml, /★/)

  console.log('offer card render: passed')
} finally {
  await server.close()
}
