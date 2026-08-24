import { onUnmounted, ref } from 'vue'

import { getToken, getWorkspace } from '@/api/client'
import type { Job } from '@/types'

/**
 * Watch one background job until it finishes.
 *
 * Queue mode had no way to tell a page that anything had happened: you
 * submitted work, the request returned, and the only way to learn the outcome
 * was to reload and look. So the mode that exists to keep long work out of the
 * request thread was also the mode where the UI appeared to do nothing.
 *
 * Read with `fetch` rather than `EventSource`, which cannot send headers -
 * and this needs two, the access token and the workspace. Without them the
 * stream would have to carry the token in the URL, where it would end up in
 * every proxy log on the way.
 */
export function useJob() {
  const job = ref<Job | null>(null)
  const watching = ref(false)
  const error = ref<string | null>(null)
  let controller: AbortController | null = null

  /** Stop watching. Safe to call twice, and called on unmount. */
  function stop() {
    controller?.abort()
    controller = null
    watching.value = false
  }

  /**
   * Follow a job, calling `onDone` once it reaches a terminal state.
   *
   * Returns when the stream ends, so a caller that wants to await the outcome
   * can, and one that only wants the reactive `job` need not.
   */
  async function watch(jobId: string, onDone?: (job: Job) => void): Promise<Job | null> {
    stop()
    controller = new AbortController()
    watching.value = true
    error.value = null

    const token = getToken()
    const workspace = getWorkspace()
    try {
      const response = await fetch(`/api/v1/jobs/${jobId}/events`, {
        signal: controller.signal,
        headers: {
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
          ...(workspace ? { 'X-Workspace': workspace } : {}),
          Accept: 'text/event-stream',
        },
      })
      if (!response.ok || !response.body) {
        throw new Error(`the job stream answered ${response.status}`)
      }

      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        //  Server-sent events are separated by a blank line; anything after
        //  the last one is a partial event and waits for the next chunk.
        const chunks = buffer.split('\n\n')
        buffer = chunks.pop() ?? ''
        for (const chunk of chunks) {
          const data = chunk
            .split('\n')
            .filter((line) => line.startsWith('data: '))
            .map((line) => line.slice(6))
            .join('')
          if (!data) continue
          const payload = JSON.parse(data) as Job
          if (!payload.id) continue
          job.value = payload
          if (['succeeded', 'failed', 'cancelled'].includes(payload.status)) {
            onDone?.(payload)
            return payload
          }
        }
      }
      return job.value
    } catch (caught) {
      //  Aborting is how `stop()` works, so it is not a failure.
      if ((caught as Error).name !== 'AbortError') {
        error.value = (caught as Error).message
      }
      return job.value
    } finally {
      watching.value = false
      controller = null
    }
  }

  //  A page that navigates away must not leave a connection open behind it.
  onUnmounted(stop)

  return { job, watching, error, watch, stop }
}
