import assert from 'node:assert/strict'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer } from 'vite'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const server = await createServer({ root, appType: 'custom', server: { middlewareMode: true } })
try {
  const { navGroupsFor } = await server.ssrLoadModule('/src/components/Layout.tsx')

  const userGroups = navGroupsFor(false)
  const userPaths = userGroups.flatMap((group) => group.items.map((item) => item.to))
  assert.equal(userGroups.length, 1, 'USER deve ter um único grupo de navegação (sem seção Administração)')
  assert.ok(userPaths.includes('/app'), 'USER deve enxergar Início')
  assert.ok(userPaths.includes('/app/missions'), 'USER deve enxergar Missões')
  assert.ok(userPaths.includes('/app/offers'), 'USER deve enxergar Ofertas')
  assert.ok(userPaths.includes('/app/suporte'), 'USER deve enxergar Suporte')
  assert.ok(!userPaths.includes('/admin'), 'USER NUNCA deve enxergar a navegação administrativa')

  const adminGroups = navGroupsFor(true)
  const adminPaths = adminGroups.flatMap((group) => group.items.map((item) => item.to))
  assert.equal(adminGroups.length, 2, 'ADMIN deve ter dois grupos: Principal + Administração')
  assert.equal(adminGroups[0].label, 'Principal')
  assert.equal(adminGroups[1].label, 'Administração')
  for (const path of userPaths) {
    assert.ok(adminPaths.includes(path), `ADMIN deve manter todo item USER (${path})`)
  }
  assert.ok(adminPaths.includes('/admin'), 'ADMIN deve enxergar a seção Administração')

  console.log('layout nav groups: passed')
} finally {
  await server.close()
}
