/**
 * Layout check: does every page hold together at every width, in both themes?
 *
 * This existed as a harness that got rebuilt from memory whenever somebody
 * needed it, which meant it was never run unless somebody already suspected a
 * problem - the opposite of what a check is for. It is committed now, and it
 * fails loudly rather than reporting.
 *
 * What it looks for is the small set of faults that are objectively wrong
 * rather than a matter of taste: the page scrolling sideways, content escaping
 * its container without a scroller to hold it, and content clipped by
 * `overflow: hidden` with no way to reach it. Everything else - spacing,
 * balance, hierarchy - is a judgement a person has to make by looking.
 *
 * The browser is the one thing this cannot supply itself. `playwright-core`
 * drives a Chrome that is already installed rather than downloading its own,
 * so the cost is one dev dependency and no browser download; without it, the
 * script says exactly that and exits without failing the build.
 *
 *   npm run check:layout                  # every page, both themes
 *   npm run check:layout -- --width 1440  # one width, while iterating
 *   npm run check:layout -- --shots out   # write screenshots too
 */

import { mkdirSync } from 'node:fs'
import { join } from 'node:path'

import {
  BREAKPOINTS,
  ROUTES,
  findChrome,
  firstLine,
  loadPlaywright,
  resolveBase,
  signIn,
} from './app-under-test.mjs'

let BASE = ''

//  Where Chrome usually is. Overridable, because "usually" is doing a lot of
//  work in a sentence about three operating systems.
const args = process.argv.slice(2)
const flag = (name) => {
  //  Both spellings, because `--width=1440` silently doing nothing is worse
  //  than it not being supported at all.
  const joined = args.find((arg) => arg.startsWith(`--${name}=`))
  if (joined) return joined.slice(name.length + 3)
  const at = args.indexOf(`--${name}`)
  return at === -1 ? null : args[at + 1]
}
const only = flag('width') ? [Number(flag('width'))] : BREAKPOINTS
const shots = flag('shots')

/**
 * Faults a person would object to on sight, found without judgement calls.
 *
 * SVG internals are excluded because a rotated axis label's bounding box lies,
 * and so is anything inside a scroll container, because wide content that
 * scrolls is correct rather than broken.
 */
const AUDIT = () => {
  const vw = document.documentElement.clientWidth
  const faults = []
  const seen = new Set()
  const describe = (el) => {
    const cls =
      typeof el.className === 'string' && el.className.trim()
        ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.')
        : ''
    const text = (el.textContent || '').trim().replace(/\s+/g, ' ').slice(0, 40)
    return `${el.tagName.toLowerCase()}${cls}${text ? ` "${text}"` : ''}`
  }
  const add = (kind, detail) => {
    const key = `${kind}|${detail}`
    if (seen.has(key)) return
    seen.add(key)
    faults.push({ kind, detail })
  }

  if (document.documentElement.scrollWidth > vw + 1) {
    add('page-scrolls-sideways', `${document.documentElement.scrollWidth}px in ${vw}px`)
  }

  for (const el of document.querySelectorAll('body *')) {
    if (el.closest('svg')) continue
    const style = getComputedStyle(el)
    if (style.display === 'none' || style.visibility === 'hidden') continue
    const box = el.getBoundingClientRect()
    if (box.width === 0 || box.height === 0) continue

    if (box.right > vw + 1 && style.position !== 'fixed') {
      let scrolls = false
      for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
        const ox = getComputedStyle(a).overflowX
        if (ox === 'auto' || ox === 'scroll') {
          scrolls = true
          break
        }
      }
      if (!scrolls) add('overflows-viewport', `${describe(el)} right=${Math.round(box.right)}`)
    }

    const clipsX = style.overflowX === 'hidden'
    const clipsY = style.overflowY === 'hidden'
    if (
      (clipsX && el.scrollWidth > el.clientWidth + 2) ||
      (clipsY && el.scrollHeight > el.clientHeight + 2)
    ) {
      if (!style.textOverflow.includes('ellipsis') && style.webkitLineClamp === 'none') {
        add('clipped', describe(el))
      }
    }
  }
  return faults
}


async function audit(page) {
  //  A route that settles by navigating - a redirect, a guard sending an
  //  unauthorised visitor away - destroys the context the audit runs in. That
  //  is not a layout fault; it just means the page to measure is the one it
  //  landed on, so let it land and ask again.
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await page.evaluate(AUDIT)
    } catch (error) {
      const navigated = /Execution context was destroyed/.test(String(error))
      if (!navigated || attempt >= 2) throw error
      await page.waitForLoadState('networkidle').catch(() => {})
      await page.waitForTimeout(900)
    }
  }
}


async function main() {
  const chromium = await loadPlaywright()
  if (!chromium) return 0
  const executablePath = findChrome()
  if (!executablePath) return 0
  BASE = await resolveBase()
  if (!BASE) return 0

  const browser = await chromium.launch({ executablePath, headless: true })
  let total = 0

  try {
    for (const theme of ['light', 'dark']) {
      for (const width of only) {
        const context = await browser.newContext({ viewport: { width, height: 900 } })
        const page = await context.newPage()

        await signIn(page, BASE)
        await page.evaluate(
          (value) => localStorage.setItem('flux-dark', value),
          theme === 'dark' ? 'true' : 'false',
        )

        if (shots) mkdirSync(join(shots, theme, String(width)), { recursive: true })

        for (const [name, path] of ROUTES) {
          await page.goto(BASE + path, { waitUntil: 'networkidle' }).catch(() => {})
          //  Charts and tables settle after their data arrives; auditing before
          //  that measures a loading skeleton.
          await page.waitForTimeout(1100)

          const faults = await audit(page).catch((error) => {
            console.log('')
            console.log(`[${theme} ${width}] ${name} could not be audited`)
            console.log(`   ${firstLine(error)}`)
            return [{ kind: 'unauditable', detail: name }]
          })
          if (shots) {
            await page.screenshot({
              path: join(shots, theme, String(width), `${name}.png`),
              fullPage: true,
            })
          }
          if (faults.length) {
            console.log(`\n[${theme} ${width}] ${name}`)
            for (const fault of faults) console.log(`   ${fault.kind}: ${fault.detail}`)
            total += faults.length
          }
        }
        await context.close()
      }
    }
  } finally {
    await browser.close()
  }

  const widths = only.join(', ')
  if (total) {
    console.log(`\n${total} layout problems across ${widths}`)
    return 1
  }
  console.log(`No layout problems across ${widths} in light and dark.`)
  return 0
}

process.exit(
  await main().catch((error) => {
    //  Exiting 0 on a crash would let the check pass by failing, which is the
    //  one outcome a committed check must never have.
    console.error(`Layout check failed to run: ${firstLine(error)}`)
    return 1
  }),
)
