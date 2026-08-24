/** Session state: who is signed in, and what they may do. */

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'

import { auth as authApi } from '@/api'
import { ApiError, getToken, setToken, setUnauthenticatedHandler } from '@/api/client'
import type { UserAccount } from '@/types'

export const useAuthStore = defineStore('auth', () => {
  const user = ref<UserAccount | null>(null)
  const authEnabled = ref(true)
  const ready = ref(false)
  //  The backend answered nothing, as distinct from answering "not you".
  const unreachable = ref(false)

  const isSignedIn = computed(() => !authEnabled.value || user.value !== null)
  const permissions = computed(() => new Set(user.value?.permissions ?? []))

  /** Whether the current account holds a permission, e.g. `model:write`. */
  function may(permission: string): boolean {
    if (!authEnabled.value) return true
    return permissions.value.has(permission)
  }

  const isAdmin = computed(() => may('platform:admin'))

  async function initialise() {
    try {
      const config = await authApi.config()
      authEnabled.value = config.auth_enabled
    } catch {
      authEnabled.value = true
    }
    if (!authEnabled.value) {
      ready.value = true
      return
    }
    if (!getToken()) {
      //  No token is not a failed request waiting to happen: it is the answer.
      //  Asking anyway put a 401 in the console on every first load, which is
      //  how a console stops being somewhere real errors are noticed.
      user.value = null
      ready.value = true
      return
    }
    try {
      user.value = await authApi.me()
    } catch (error) {
      //  Only an authentication failure means the token is worthless. A network
      //  error or a 5xx means the server could not answer — throwing the token
      //  away for that logs somebody out because the backend restarted, and
      //  loses whatever they were in the middle of.
      const status = error instanceof ApiError ? error.status : 0
      if (status === 401 || status === 403) {
        user.value = null
        setToken(null)
      } else {
        unreachable.value = true
      }
    }
    ready.value = true
  }

  /** Retry establishing the session after the backend comes back. */
  async function reconnect() {
    unreachable.value = false
    ready.value = false
    await initialise()
    return user.value !== null
  }

  async function signIn(email: string, password: string) {
    const response = await authApi.login(email, password)
    setToken(response.access_token)
    user.value = response.user
    return response.user
  }

  function signOut() {
    setToken(null)
    user.value = null
  }

  async function refresh() {
    if (!authEnabled.value) return
    user.value = await authApi.me()
  }

  //  A 401 from anywhere means the token is gone or stale; drop it so the
  //  router guard sends the person to the sign-in page.
  setUnauthenticatedHandler(() => {
    setToken(null)
    user.value = null
  })

  return {
    user,
    authEnabled,
    ready,
    unreachable,
    reconnect,
    isSignedIn,
    isAdmin,
    may,
    initialise,
    signIn,
    signOut,
    refresh,
  }
})
