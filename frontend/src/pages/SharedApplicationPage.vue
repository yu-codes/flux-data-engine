<template>
  <q-page class="shared">
    <AsyncSection :pending="loading" :error="error" title="Could not open this link">
      <!--
        A revoked link and one that never existed are the same thing here on
        purpose, so the message says what the reader can do rather than which
        of the two happened.
      -->
      <EmptyState
        v-if="!application"
        message="This link is not valid"
        hint="It may have been revoked, or the application unpublished."
        icon="link_off"
      />

      <!--
        Guarded as a whole rather than field by field. Optional chaining on
        each value rendered an invalid link as a header with blanks in it -
        "Shared view · dashboard(s) built on dataset(s)" - which reads as a
        broken page rather than as a link that no longer works.
      -->
      <template v-if="application">
      <header class="shared__header">
        <h1 class="shared__title">{{ application.name }}</h1>
        <p v-if="application.description" class="shared__subtitle">
          {{ application.description }}
        </p>
        <!--
          Said plainly, because a reader arriving from a link has no other way
          to tell what they are looking at or how much of it there is.
        -->
        <p class="fx-meta">
          Shared view · {{ application.built_from.dashboards }} dashboard(s) built on
          {{ application.built_from.datasets }} dataset(s)
        </p>
      </header>

      <RenderedDashboards :dashboards="application.dashboards" />
      </template>
    </AsyncSection>
  </q-page>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import AsyncSection from '@/components/AsyncSection.vue'
import EmptyState from '@/components/EmptyState.vue'
import RenderedDashboards from '@/components/RenderedDashboards.vue'
import type { RenderedBoard } from '@/components/RenderedDashboards.vue'

/**
 * What somebody outside the platform sees.
 *
 * Deliberately its own page rather than the dashboard page with the chrome
 * hidden: a reader here has no account, no workspace and no navigation, and a
 * page built for somebody who does would spend most of its area offering
 * things that would refuse them.
 *
 * It calls the public endpoint directly rather than through the API client,
 * because the client attaches a token and a workspace header - neither of
 * which this reader has, and neither of which this endpoint wants.
 */
interface SharedApplication {
  name: string
  description: string
  slug: string
  dashboards: RenderedBoard[]
  built_from: { models: number; datasets: number; dashboards: number }
}

const route = useRoute()
const application = ref<SharedApplication | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    const response = await fetch(`/api/v1/public/applications/${route.params.token}`)
    if (!response.ok) {
      //  A revoked link and one that never existed look the same on purpose,
      //  so the message says what the reader can act on rather than guessing.
      application.value = null
      return
    }
    application.value = await response.json()
    document.title = `${application.value?.name} · shared`
  } catch (caught) {
    error.value = (caught as Error).message
  } finally {
    loading.value = false
  }
})
</script>

<style scoped>
.shared {
  max-width: 1200px;
  margin: 0 auto;
  padding: var(--fx-space-6) var(--fx-space-4);
}

.shared__header {
  margin-bottom: var(--fx-space-5);
}

.shared__title {
  font-size: var(--fx-text-2xl);
  font-weight: 600;
  margin: 0 0 var(--fx-space-1);
}

.shared__subtitle {
  margin: 0 0 var(--fx-space-1);
  opacity: var(--fx-ink);
}

</style>
