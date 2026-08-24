/**
 * A cache small enough to reason about.
 *
 * Every page fetched everything it needed on mount, including the parts that
 * cannot change while the tab is open: the provider catalogue, the transform
 * vocabulary, the list of chart types. Opening four pages asked the backend
 * the same four questions four times, and each answer arrived after a
 * round-trip the user waited through.
 *
 * So: remember answers for a while, share the request when two callers ask at
 * once, and forget everything the moment something is written or the workspace
 * changes. That last part is what makes this safe to use - a cache that is
 * never invalidated is just a way to show stale data confidently.
 */

const DEFAULT_TTL_MS = 5 * 60 * 1000

type Entry = { at: number; value: unknown }

const entries = new Map<string, Entry>()
//  Requests already in the air, so that three components mounting together
//  produce one request rather than three.
const inflight = new Map<string, Promise<unknown>>()

/** Which workspace an answer belongs to. Two workspaces are two answers. */
let scope = 'default'

export function setCacheScope(workspace: string | null) {
  const next = workspace ?? 'default'
  if (next === scope) return
  scope = next
  //  Nothing cached under the old workspace is true under the new one.
  entries.clear()
  inflight.clear()
}

function keyFor(key: string): string {
  return `${scope}::${key}`
}

/**
 * The cached answer, or a fresh one.
 *
 * `ttl` is how long an answer stays good. Vocabulary endpoints can use the
 * default; anything a person edits should not be cached at all.
 */
export async function cached<T>(
  key: string,
  loader: () => Promise<T>,
  ttl: number = DEFAULT_TTL_MS,
): Promise<T> {
  const scoped = keyFor(key)
  const hit = entries.get(scoped)
  if (hit && Date.now() - hit.at < ttl) return hit.value as T

  const pending = inflight.get(scoped)
  if (pending) return pending as Promise<T>

  const request = loader()
    .then((value) => {
      entries.set(scoped, { at: Date.now(), value })
      return value
    })
    .finally(() => {
      inflight.delete(scoped)
    })

  inflight.set(scoped, request)
  return request as Promise<T>
}

/**
 * Forget cached answers.
 *
 * With a prefix, forgets the ones whose key starts with it - `/models` covers
 * `/models` and `/models/abc`. With nothing, forgets everything, which is the
 * right response to a write whose effects are not local: publishing a version
 * changes what half the platform reports.
 */
export function invalidate(prefix?: string) {
  if (!prefix) {
    entries.clear()
    return
  }
  const scopedPrefix = keyFor(prefix)
  for (const key of entries.keys()) {
    if (key.startsWith(scopedPrefix)) entries.delete(key)
  }
}
