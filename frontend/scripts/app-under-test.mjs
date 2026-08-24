/**
 * The things both browser checks need: where the app is, how to open it, and
 * which pages there are.
 *
 * Shared so the two cannot disagree about the route list. A page that only one
 * of them visits is a page that is only half checked, and the half it is
 * missing is usually the half that breaks.
 */

import { existsSync } from 'node:fs'

export const BREAKPOINTS = [375, 768, 1024, 1440, 1920]

/** Every page reachable from the sidebar, as [name, path]. */
export const ROUTES = [
  ['overview', '/'],
  ['sources', '/sources'],
  ['datasets', '/datasets'],
  ['pipelines', '/pipelines'],
  ['explore', '/explore'],
  ['visualizations', '/visualizations'],
  ['dashboards', '/dashboards'],
  ['models', '/models'],
  ['experiments', '/experiments'],
  ['executions', '/executions'],
  ['results', '/results'],
  ['evaluation', '/evaluation'],
  ['reports', '/reports'],
  ['applications', '/applications'],
  ['schedules', '/schedules'],
  ['users', '/users'],
  ['audit', '/audit'],
  ['settings', '/settings'],
]

const CHROME_CANDIDATES = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].filter(Boolean)

export const EMAIL = process.env.FLUX_EMAIL ?? 'admin@flux.local'
export const PASSWORD = process.env.FLUX_PASSWORD ?? 'flux-admin'

export async function loadPlaywright() {
  try {
    const { chromium } = await import('playwright-core')
    return chromium
  } catch {
    console.log(
      'Skipped: playwright-core is not installed. `npm i -D playwright-core` to run this.',
    )
    return null
  }
}

export function findChrome() {
  const found = CHROME_CANDIDATES.find((path) => existsSync(path))
  if (!found) {
    console.log('Skipped: no Chrome found. Set CHROME_PATH to point at one.')
  }
  return found
}

/**
 * The base URL, checked to be answering.
 *
 * On Windows `localhost` often resolves to ::1 first, which Docker may not be
 * listening on, so the v4 address is tried before giving up. Returns null when
 * nothing answers, which is a skip rather than a failure: not every machine
 * running the unit tests has the stack up.
 */
export async function resolveBase() {
  const stated = process.env.FLUX_BASE_URL ?? 'http://localhost:3001'
  for (const candidate of [stated, stated.replace('localhost', '127.0.0.1')]) {
    try {
      await fetch(candidate, { signal: AbortSignal.timeout(4000) })
      return candidate
    } catch {
      //  Try the next one.
    }
  }
  console.log(`Skipped: nothing answering at ${stated}. Start the frontend first.`)
  return null
}

/** Sign in, so the pages under test are the ones a person actually sees. */
export async function signIn(page, base) {
  await page.goto(`${base}/login`, { waitUntil: 'networkidle' })
  if (!page.url().includes('/login')) return
  await page.fill('input[type="email"]', EMAIL)
  await page.fill('input[type="password"]', PASSWORD)
  await page.keyboard.press('Enter')
  await page.waitForURL((url) => !url.pathname.includes('/login'), { timeout: 20000 })
}

export function firstLine(error) {
  return String(error).split('\n')[0].trim()
}
