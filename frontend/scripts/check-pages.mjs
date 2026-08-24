/**
 * Does every page actually work?
 *
 * This exists because a page can be completely broken and pass every other
 * check the project has. `check:layout` measures geometry, so it reported "no
 * layout problems" on a page whose only content was a failed request. The unit
 * tests build their own schema with `create_all`, so they never notice that the
 * database the application is actually talking to is a migration behind the
 * code — which is exactly how `/experiments` and `/evaluation` came to answer
 * 500 to every visitor while everything else said the platform was fine.
 *
 * So this one asks the only question that matters to somebody using it: I
 * opened the page — did it work?
 *
 *   * no request answered 4xx or 5xx
 *   * nothing was written to the console as an error
 *   * no uncaught exception reached the page
 *   * the page is not showing one of the app's own failure states
 *
 * Any of those is a failure, named with the route it happened on.
 */

import {
  ROUTES,
  findChrome,
  firstLine,
  loadPlaywright,
  resolveBase,
  signIn,
} from './app-under-test.mjs'

//  The app's own ways of saying "this did not load", as they appear on screen.
const FAILURE_TEXT = [
  /Could not load/i,
  /Could not open/i,
  /Cannot reach the server/i,
  /did not respond in time/i,
  /Something went wrong/i,
  /Internal Server Error/i,
]

/** Requests a working page still makes that are not the page's fault. */
function ignorable(url, status) {
  //  A favicon is the browser's business, not the application's.
  if (url.endsWith('/favicon.ico')) return true
  //  Vite's dev-time probes during hot reload.
  if (url.includes('/@vite/') || url.includes('/__vite')) return true
  //  401 on the sign-in page is the answer to "am I signed in", not a fault.
  return status === 401 && url.includes('/auth/')
}

async function main() {
  const chromium = await loadPlaywright()
  if (!chromium) return 0
  const executablePath = findChrome()
  if (!executablePath) return 0
  const base = await resolveBase()
  if (!base) return 0

  const browser = await chromium.launch({ executablePath, headless: true })
  const failures = []

  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } })
    const page = await context.newPage()

    let where = '/login'
    page.on('response', (response) => {
      const status = response.status()
      if (status < 400 || ignorable(response.url(), status)) return
      failures.push(`${where}: ${status} from ${new URL(response.url()).pathname}`)
    })
    page.on('console', (message) => {
      if (message.type() !== 'error') return
      failures.push(`${where}: console error — ${message.text().slice(0, 140)}`)
    })
    page.on('pageerror', (error) => {
      failures.push(`${where}: uncaught — ${firstLine(error)}`)
    })

    await signIn(page, base)
    //  Anything before this point was the sign-in flow, which has its own
    //  check; from here on every complaint belongs to a page.
    failures.length = 0

    for (const [name, path] of ROUTES) {
      where = name
      await page.goto(base + path, { waitUntil: 'networkidle' }).catch((error) => {
        failures.push(`${name}: could not open — ${firstLine(error)}`)
      })
      //  Lists and charts arrive after their data; judging before that judges
      //  a loading skeleton.
      await page.waitForTimeout(1200)

      const text = await page.locator('body').innerText().catch(() => '')
      for (const pattern of FAILURE_TEXT) {
        const found = text.match(pattern)
        if (found) {
          failures.push(`${name}: the page is showing "${found[0]}"`)
          break
        }
      }
    }
    await context.close()
  } finally {
    await browser.close()
  }

  const unique = [...new Set(failures)]
  if (unique.length) {
    const pages = new Set(unique.map((line) => line.split(":")[0]))
    console.log(
      `\n${unique.length} problem(s) on ${pages.size} page(s):\n`,
    )
    for (const failure of unique) console.log(`  ${failure}`)
    console.log('')
    return 1
  }
  console.log(`All ${ROUTES.length} pages loaded without an error.`)
  return 0
}

process.exit(
  await main().catch((error) => {
    console.error(`Page check failed to run: ${firstLine(error)}`)
    return 1
  }),
)
