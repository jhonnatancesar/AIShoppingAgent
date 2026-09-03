import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router-dom'
import { createServer } from 'vite'

// A Landing inclui `ThemeToggle` (mesmo componente já usado no login) --
// ele lê `localStorage` de forma síncrona no primeiro render
// (`useState(initialTheme)`), que também roda durante SSR. Devolver um
// tema salvo válido evita o branch que tocaria `window.matchMedia` --
// `window` de propósito continua indefinido (padrão do Node), porque a
// Landing também usa `motion/react` (inclusive `useReducedMotion`/
// `useInView`, confirmados SSR-safe sem stub adicional), que só evita
// anexar listeners reais de DOM quando detecta a ausência de `window`.
globalThis.localStorage = { getItem: () => 'dark', setItem: () => {} }

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({ root, appType: 'custom', server: { middlewareMode: true } })
try {
  const { LandingPage } = await server.ssrLoadModule('/src/pages/LandingPage.tsx')
  const { RegisterPage } = await server.ssrLoadModule('/src/pages/RegisterPage.tsx')
  const { AuthProvider } = await server.ssrLoadModule('/src/auth/AuthContext.tsx')
  const { TooltipProvider } = await server.ssrLoadModule('/src/components/ui/tooltip.tsx')

  // `renderToStaticMarkup` nunca roda efeitos -- `AuthProvider` nunca chega a
  // buscar a sessão real, então `user` permanece `null` durante todo o
  // render síncrono. Isso reproduz fielmente o visitante anônimo, sem
  // precisar de nenhum mock de rede nem de um refactor só para teste. Pelo
  // mesmo motivo, a demonstração do Telegram nunca chega a mostrar as
  // bolhas (dependem de `useInView`/temporizadores reais de navegador) --
  // testada de verdade só na inspeção visual ao vivo; aqui provamos que o
  // rótulo de demonstração está sempre presente, independente de animação.
  // `TooltipProvider` (Subtask 12) é exigido pelo `ThemeToggle` usado nesta
  // página -- em produção ele vem de `App.tsx`, aqui precisa ser explícito.
  const html = renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      { initialEntries: ['/'] },
      React.createElement(
        TooltipProvider,
        null,
        React.createElement(AuthProvider, null, React.createElement(LandingPage)),
      ),
    ),
  )

  // 1) `/` público renderiza a Landing para visitante anônimo.
  assert.match(html, /GG Oferta/)
  assert.match(html, /Você diz o que quer comprar/)
  assert.match(html, /A gente cuida de ficar de olho no preço/)

  // Acessibilidade: exatamente um H1, nenhum nível pulado (nunca H1 -> H3
  // direto) -- achado real corrigido nesta revisão.
  const headingLevels = [...html.matchAll(/<h([1-6])[ >]/g)].map((match) => Number(match[1]))
  assert.equal(headingLevels.filter((level) => level === 1).length, 1, 'deve haver exatamente um H1')
  for (let i = 1; i < headingLevels.length; i += 1) {
    assert.ok(
      headingLevels[i] <= headingLevels[i - 1] + 1,
      `hierarquia de heading não pode pular nível (${headingLevels[i - 1]} -> ${headingLevels[i]})`,
    )
  }

  // 2) e 3) CTAs levam para as rotas reais de login/cadastro.
  const loginLinks = html.match(/href="\/login"/g) || []
  const cadastroLinks = html.match(/href="\/cadastro"/g) || []
  assert.ok(loginLinks.length >= 2, 'CTA "Entrar" deve aparecer em pelo menos 2 pontos (header, hero/CTA final, footer)')
  assert.ok(cadastroLinks.length >= 2, 'CTA "Criar conta" deve aparecer em pelo menos 2 pontos')

  // 4) GG Oferta é o branding público -- nunca AIShoppingAgent.
  assert.doesNotMatch(html, /AIShoppingAgent/)

  // 6) e 7) só as seis lojas reais aparecem, nunca futuras.
  for (const store of ['Amazon', 'Mercado Livre', 'Magalu', 'KaBuM!', 'Pichau', 'Terabyte']) {
    assert.match(html, new RegExp(store.replace('!', '!')), `loja real ${store} deve aparecer`)
  }
  assert.doesNotMatch(html, /Shopee/i)
  assert.doesNotMatch(html, /AliExpress/i)

  // 8) qualquer conteúdo demonstrativo é marcado como tal, sempre presente
  // independente do estado da animação (a legenda do widget e os rótulos
  // "Exemplo" das composições da narrativa não dependem de temporizador).
  assert.match(html, /Demonstração de uma conversa no Telegram/)
  const exemploLabels = html.match(/>Exemplo</g) || []
  assert.ok(exemploLabels.length >= 2, 'as duas composições da narrativa devem se rotular como "Exemplo"')

  // 9) zero fake social proof/métricas/depoimento/urgência artificial.
  assert.doesNotMatch(html, /revolucion[áa]rio/i)
  assert.doesNotMatch(html, /melhor plataforma/i)
  assert.doesNotMatch(html, /economize milhares/i)
  assert.doesNotMatch(html, /milhares de usu[áa]rios/i)
  assert.doesNotMatch(html, /em poucos minutos/i)
  assert.doesNotMatch(html, /menor pre[çc]o da internet/i)
  assert.doesNotMatch(html, /pre[çc]o garantido/i)
  assert.doesNotMatch(html, /\d+[.,]?\d*\s*(estrelas|avalia[çc][õo]es)/i)
  assert.doesNotMatch(html, /t\.me\//i)
  assert.doesNotMatch(html, /@\w+bot/i)

  console.log('landing page render: passed')

  // 5) AIShoppingAgent também não pode aparecer no cadastro (fluxo
  // Landing -> Criar conta), achado real corrigido nesta revisão.
  const registerHtml = renderToStaticMarkup(
    React.createElement(
      MemoryRouter,
      { initialEntries: ['/cadastro'] },
      React.createElement(
        TooltipProvider,
        null,
        React.createElement(AuthProvider, null, React.createElement(RegisterPage)),
      ),
    ),
  )
  assert.doesNotMatch(registerHtml, /AIShoppingAgent/)
  assert.match(registerHtml, /GG Oferta/)
  console.log('register page branding: passed')
} finally {
  await server.close()
}
