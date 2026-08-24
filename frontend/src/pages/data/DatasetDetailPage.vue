<template>
  <q-page class="page-shell">
    <PageHeader :title="dataset?.name ?? 'Dataset'" :subtitle="dataset?.description">
      <template #actions>
        <q-btn no-caps flat dense icon="travel_explore" label="Explore" :to="exploreLink" />
        <q-btn no-caps
          v-if="dataset?.source_id"
          flat
          dense
          icon="refresh"
          label="New version"
          :loading="refreshing"
          @click="refresh"
        />
      </template>
    </PageHeader>

    <div v-if="dataset" class="row q-col-gutter-md">
      <div class="col-12 col-md-4">
        <SectionCard
          title="Schema"
          :subtitle="`${dataset.schema_fields.length} fields`"
          class="q-mb-md"
          tight
        >
            <q-list dense class="fx-list">
              <q-item v-for="field in dataset.schema_fields" :key="field.name" class="q-px-none">
                <q-item-section>
                  <q-item-label>{{ field.name }}</q-item-label>
                </q-item-section>
                <q-item-section side>
                  <span class="fx-tag fx-tag--code">{{ field.type }}</span>
                </q-item-section>
              </q-item>
            </q-list>
        </SectionCard>

        <SectionCard
          title="Versions"
          subtitle="Immutable — re-reading the source appends a new one"
          tight
        >
            <q-list dense separator class="fx-list">
              <q-item
                v-for="version in dataset.versions"
                :key="version.id"
                clickable
                :active="version.id === activeVersionId"
                @click="selectVersion(version.id)"
              >
                <q-item-section>
                  <q-item-label>v{{ version.version }}</q-item-label>
                  <q-item-label caption>
                    {{ version.row_count.toLocaleString() }} rows ·
                    {{ version.column_count }} cols
                  </q-item-label>
                </q-item-section>
                <q-item-section side class="text-caption">
                  {{ new Date(version.created_at).toLocaleDateString() }}
                </q-item-section>
              </q-item>
            </q-list>
        </SectionCard>
      </div>

      <div class="col-12 col-md-8">
        <SectionCard title="Rows" :subtitle="rowsSubtitle" flush>
            <DataTable
              v-if="preview"
              :rows="preview.rows"
              :fields="preview.columns"
              :loading="loadingPreview"
            />
        </SectionCard>

        <!--
          The question a person actually asks about a table they did not build:
          where did this come from, and what would I break by changing it.
        -->
        <SectionCard
          v-if="dataset"
          title="Lineage"
          subtitle="How this dataset came to exist, and what reads it"
          class="q-mt-md"
        >
          <LineageGraph kind="dataset" :node-id="dataset.id" />
        </SectionCard>
      </div>
    </div>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import { data as dataApi } from '@/api'
import DataTable from '@/components/DataTable.vue'
import PageHeader from '@/components/PageHeader.vue'
import LineageGraph from '@/components/LineageGraph.vue'
import SectionCard from '@/components/SectionCard.vue'
import type { DatasetDetail, Preview } from '@/types'

const $q = useQuasar()
const route = useRoute()
const dataset = ref<DatasetDetail | null>(null)
const preview = ref<Preview | null>(null)
const activeVersionId = ref('')
const loadingPreview = ref(false)
const refreshing = ref(false)

const exploreLink = computed(() => ({
  name: 'explore',
  query: { version: activeVersionId.value },
}))

/** Name the version being previewed, and how much of it is on screen. */
const rowsSubtitle = computed(() => {
  const version = dataset.value?.versions.find((entry) => entry.id === activeVersionId.value)
  if (!version) return undefined
  const shown = preview.value?.rows.length ?? 0
  return `v${version.version} · first ${shown} of ${version.row_count.toLocaleString()} rows`
})

async function load() {
  try {
    dataset.value = await dataApi.getDataset(String(route.params.id))
    activeVersionId.value = dataset.value.current_version_id ?? dataset.value.versions[0]?.id ?? ''
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
}

async function loadPreview() {
  if (!activeVersionId.value) return
  loadingPreview.value = true
  try {
    preview.value = await dataApi.previewVersion(activeVersionId.value, 100)
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loadingPreview.value = false
  }
}

function selectVersion(id: string) {
  activeVersionId.value = id
}

async function refresh() {
  refreshing.value = true
  try {
    await dataApi.refreshDataset(String(route.params.id))
    $q.notify({ type: 'positive', message: 'New version created' })
    await load()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    refreshing.value = false
  }
}

watch(activeVersionId, loadPreview)
onMounted(load)
</script>
