<template>
  <q-page class="row items-center justify-center login-page">
    <q-card flat class="flux-card login-card">
      <q-card-section class="text-center q-pb-none">
        <div class="text-h6 text-weight-bold">flux-data-engine</div>
        <div class="text-caption q-mt-xs" style="opacity: 0.7">
          Data → Model → Execution → Result → Application
        </div>
      </q-card-section>

      <!--
        A password form is the wrong thing to offer when the server is not
        answering: no credentials can succeed, so the only thing it can produce
        is a person doubting their password. Say what is actually wrong.
      -->
      <q-card-section v-if="store.unreachable" class="q-pt-lg">
        <div class="login-offline" role="alert">
          <q-icon name="cloud_off" size="24px" />
          <div>
            <div class="login-offline__title">Cannot reach the server</div>
            <div class="fx-meta">
              Your session is kept, so signing in again will not be necessary once
              it responds.
            </div>
          </div>
        </div>
        <q-btn
          no-caps
          unelevated
          color="primary"
          class="full-width fx-btn q-mt-md"
          icon="refresh"
          label="Try again"
          :loading="retrying"
          @click="retryConnection"
        />
      </q-card-section>

      <q-card-section v-else>
        <!--
          `gap`, not `q-gutter-md`. The gutter works by putting a negative
          margin on the container and a positive one on every child, so a
          child with `width: 100%` ends up 100% of the widened container and
          hangs over the card's padding - which is what the sign-in button was
          doing while the inputs, being auto-width, looked fine beside it.
        -->
        <q-form class="login-form" @submit.prevent="submit">
          <q-input
            v-model="email"
            label="Email"
            type="email"
            dense
            outlined
            autofocus
            :error="Boolean(error)"
            autocomplete="username"
          />
          <q-input
            v-model="password"
            label="Password"
            :type="reveal ? 'text' : 'password'"
            dense
            outlined
            :error="Boolean(error)"
            :error-message="error ?? undefined"
            autocomplete="current-password"
          >
            <template #append>
              <q-icon
                :name="reveal ? 'visibility_off' : 'visibility'"
                class="cursor-pointer"
                @click="reveal = !reveal"
              />
            </template>
          </q-input>
          <!--
            `fx-btn` for the platform's own padding and weight, and the
            standard size rather than Quasar's `sm`, which shrinks the label
            below the type scale. The button beside it in the offline branch
            has always had both; this one had neither.
          -->
          <q-btn
            no-caps
            type="submit"
            color="primary"
            unelevated
            class="full-width fx-btn login-form__submit"
            label="Sign in"
            :loading="busy"
          />
        </q-form>
      </q-card-section>

      <q-card-section class="q-pt-none text-caption" style="opacity: 0.6">
        A fresh installation creates one administrator on first start; its address and
        password come from <span class="mono">FLUX_BOOTSTRAP_ADMIN_*</span>.
      </q-card-section>
    </q-card>
  </q-page>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const route = useRoute()
const store = useAuthStore()

const email = ref('')
const password = ref('')
const reveal = ref(false)
const busy = ref(false)
const error = ref<string | null>(null)
const retrying = ref(false)

async function retryConnection() {
  retrying.value = true
  try {
    if (await store.reconnect()) await router.replace('/dashboard')
  } finally {
    retrying.value = false
  }
}

async function submit() {
  busy.value = true
  error.value = null
  try {
    await store.signIn(email.value, password.value)
    const target = (route.query.redirect as string) || '/dashboard'
    await router.replace(target)
  } catch (caught) {
    error.value = (caught as Error).message
  } finally {
    busy.value = false
  }
}
</script>

<style scoped>
/*
  The form lays itself out. Gap spaces the fields without the negative-margin
  trick that made a full-width child overhang the card.
*/
.login-form {
  display: flex;
  flex-direction: column;
  gap: var(--fx-space-4);
}

/*
  Tall enough to sit with the inputs rather than under them. A 30px button
  beside a 40px field reads as an afterthought, and this is the only control
  on the page.
*/
.login-form__submit {
  min-height: 40px;
  margin-top: var(--fx-space-1);
}

.login-offline {
  display: flex;
  align-items: flex-start;
  gap: var(--fx-space-3);
  color: var(--fx-bad);
}

.login-offline__title {
  font-size: var(--fx-text-sm);
  font-weight: 600;
}

.login-page {
  min-height: 100vh;
}

.login-card {
  width: 100%;
  max-width: 380px;
}
</style>
