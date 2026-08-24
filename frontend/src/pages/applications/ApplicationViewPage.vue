<template>
  <q-page class="fx-page">
    <PageHeader
      :title="application?.name ?? 'Application'"
      :subtitle="application?.description"
    >
      <template #actions>
        <q-btn flat dense icon="arrow_back" label="Applications" to="/applications" />
      </template>
    </PageHeader>

    <AsyncSection :pending="loading" :error="error" title="Could not open this application">
      <template v-if="application">
        <p class="fx-meta q-mb-md">
          {{ application.built_from.dashboards }} dashboard(s) built on
          {{ application.built_from.datasets }} dataset(s), using
          {{ application.built_from.models }} model(s)
        </p>
        <!--
          Tools first: an application that can answer a question is more use
          than one that can only show yesterday's chart.
        -->
        <ModelTool
          v-for="tool in application.tools"
          :key="tool.model_id"
          :tool="tool"
          :datasets="application.datasets"
          class="q-mb-md"
        />

        <RenderedDashboards
          :dashboards="application.dashboards"
          :empty-hint="
            application.tools.length
              ? 'This application has no dashboards — the tools above are what it offers.'
              : 'Add a dashboard or a model to this application and it will appear here.'
          "
        />
      </template>
    </AsyncSection>
  </q-page>
</template>

<script setup lang="ts">
/**
 * An application, opened.
 *
 * A composed application used to be three lists of ids and nothing to look at:
 * it could be built, published and shared, and the only way for its own author
 * to see it was to open the share link in another browser. This is the page
 * that was missing, and it renders through the same component the shared view
 * uses so the two cannot drift apart.
 */
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'

import { applications as appsApi } from '@/api'
import AsyncSection from '@/components/AsyncSection.vue'
import PageHeader from '@/components/PageHeader.vue'
import ModelTool from '@/components/ModelTool.vue'
import RenderedDashboards from '@/components/RenderedDashboards.vue'
import type { ApplicationView } from '@/types'

const route = useRoute()
const application = ref<ApplicationView | null>(null)
const loading = ref(true)
const error = ref<string | null>(null)

onMounted(async () => {
  try {
    application.value = await appsApi.view(String(route.params.id))
  } catch (caught) {
    error.value = (caught as Error).message
  } finally {
    loading.value = false
  }
})
</script>
