/** Thin fetch wrapper. The backend's error envelope is surfaced verbatim. */

import { invalidate, setCacheScope } from './cache'

const BASE = '/api/v1'
const TOKEN_KEY = 'flux-access-token'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string = 'error',
    readonly details: Record<string, unknown> = {},
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** Called when a request comes back unauthenticated, so the app can sign out. */
let onUnauthenticated: (() => void) | null = null

export function setUnauthenticatedHandler(handler: () => void) {
  onUnauthenticated = handler
}

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY)
}

export function setToken(token: string | null) {
  if (token) localStorage.setItem(TOKEN_KEY, token)
  else localStorage.removeItem(TOKEN_KEY)
}

const WORKSPACE_KEY = 'flux-workspace'

/** Which workspace the app is looking at. Absent means the default one. */
export function getWorkspace(): string | null {
  return localStorage.getItem(WORKSPACE_KEY)
}

export function setWorkspace(workspaceId: string | null) {
  if (workspaceId) localStorage.setItem(WORKSPACE_KEY, workspaceId)
  else localStorage.removeItem(WORKSPACE_KEY)
  //  Anything remembered belonged to the workspace we just left.
  setCacheScope(workspaceId)
}

//  The stored workspace is chosen before any request goes out, so the cache
//  starts in the right scope rather than in "default".
setCacheScope(getWorkspace())

function authHeaders(): Record<string, string> {
  const token = getToken()
  const workspace = getWorkspace()
  return {
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    //  Every request says which workspace it is for. A header rather than a
    //  URL prefix so that every existing route keeps working and a client
    //  that has never chosen one still lands in the default.
    ...(workspace ? { 'X-Workspace': workspace } : {}),
  }
}

/**
 * How long to wait before calling a request lost.
 *
 * Without a bound, an unreachable backend leaves every page showing a loading
 * skeleton for as long as the tab stays open: no error, no retry, nothing to
 * act on. A request that has not answered in this long is not going to, and
 * saying so is more useful than spinning.
 *
 * Generous, because executions and materialisations are legitimately slow.
 */
const TIMEOUT_MS = 30_000

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const isForm = init.body instanceof FormData
  let response: Response
  try {
    response = await fetch(`${BASE}${path}`, {
      ...init,
      signal: init.signal ?? AbortSignal.timeout(TIMEOUT_MS),
      headers: {
        ...(isForm ? {} : { 'Content-Type': 'application/json' }),
        ...authHeaders(),
        ...(init.headers ?? {}),
      },
    })
  } catch (error) {
    //  Nothing came back at all: a dropped connection, a stopped server, or a
    //  request that ran past the bound above. Status 0 marks "no response",
    //  which is what tells the session it must not discard its token.
    const timedOut = error instanceof DOMException && error.name === 'TimeoutError'
    throw new ApiError(
      timedOut
        ? 'The server did not respond in time.'
        : 'Cannot reach the server. Check that the backend is running.',
      0,
      'unreachable',
    )
  }

  if (response.status === 401 && onUnauthenticated) onUnauthenticated()
  if (response.status === 204) return undefined as T

  const text = await response.text()
  const payload = text ? tryParse(text) : null

  if (!response.ok) {
    const message =
      payload?.message ??
      (Array.isArray(payload?.detail)
        ? payload.detail.map((d: { msg: string }) => d.msg).join('; ')
        : payload?.detail) ??
      response.statusText
    throw new ApiError(message, response.status, payload?.error ?? 'error', payload?.details ?? {})
  }

  //  A write can change the answer to questions asked elsewhere - publishing a
  //  version changes what the library reports, deleting a dataset changes what
  //  a chart can be built from - so anything remembered is dropped. Cheap,
  //  because only vocabulary endpoints are cached in the first place.
  if (init.method && init.method !== 'GET') invalidate()

  return (payload ?? (text as unknown)) as T
}

function tryParse(text: string): any {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

/** Fetches a non-JSON document (a report export) as text plus its media type. */
export async function requestText(path: string): Promise<{ text: string; contentType: string }> {
  const response = await fetch(`${BASE}${path}`, { headers: authHeaders() })
  if (response.status === 401 && onUnauthenticated) onUnauthenticated()
  const text = await response.text()
  if (!response.ok) {
    const payload = tryParse(text)
    throw new ApiError(payload?.message ?? response.statusText, response.status)
  }
  return { text, contentType: response.headers.get('content-type') ?? 'text/plain' }
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'POST', body: JSON.stringify(body ?? {}) }),
  patch: <T>(path: string, body: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(body) }),
  del: (path: string) => request<void>(path, { method: 'DELETE' }),
  upload: <T>(path: string, form: FormData) =>
    request<T>(path, { method: 'POST', body: form }),
}
