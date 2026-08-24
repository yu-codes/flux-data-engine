<template>
  <q-page class="page-shell">
    <PageHeader
      title="Audit"
      subtitle="Append-only record of who changed what. Nothing rewrites these entries."
    >
      <template #actions>
        <q-btn no-caps flat dense icon="refresh" label="Refresh" :loading="loading" @click="load" />
      </template>
    </PageHeader>

    <div class="row q-col-gutter-sm q-mb-sm">
      <div class="col-6 col-md-3">
        <q-select
          v-model="resourceType"
          :options="resourceTypes"
          label="Resource"
          dense
          outlined
          clearable
          @update:model-value="load"
        />
      </div>
      <div class="col-6 col-md-3">
        <q-select
          v-model="action"
          :options="actions"
          label="Action"
          dense
          outlined
          clearable
          @update:model-value="load"
        />
      </div>
    </div>

    <SectionCard title="Change log" :subtitle="`${summary} entries`" flush>
      <template #actions>
        <SearchField v-model="term" placeholder="Search the log" />
      </template>
      <q-list separator dense class="fx-list">
        <q-item v-for="entry in filtered" :key="entry.id">
          <q-item-section avatar>
            <q-icon
              :name="entry.outcome === 'succeeded' ? 'check_circle' : 'error'"
              :color="entry.outcome === 'succeeded' ? 'positive' : 'negative'"
              size="20px"
            />
          </q-item-section>
          <q-item-section>
            <q-item-label>
              <span class="mono">{{ entry.action }}</span>
              <!--
                The log records what changed; being unable to open the thing it
                names made it a wall of ids. Types with a page link through, the
                rest still read as identifiers.
              -->
              <router-link
                v-if="entry.resource_id && routeFor(entry)"
                :to="routeFor(entry)!"
                class="fx-link chip-id q-ml-xs"
              >
                {{ entry.resource_id }}
              </router-link>
              <span v-else-if="entry.resource_id" class="text-caption chip-id">
                · {{ entry.resource_id }}
              </span>
            </q-item-label>
            <q-item-label caption>
              {{ entry.actor_email ?? 'system' }} · {{ new Date(entry.created_at).toLocaleString() }}
              <span v-if="Object.keys(entry.detail).length"> · {{ summarise(entry.detail) }}</span>
            </q-item-label>
          </q-item-section>
          <q-item-section side>
            <span class="fx-tag">{{ entry.resource_type }}</span>
          </q-item-section>
        </q-item>
      </q-list>
      <EmptyState
        v-if="!filtered.length && !loading"
        :message="isFiltering ? 'No entry matches that' : 'Nothing recorded yet'"
        icon="history"
      />
    </SectionCard>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref } from 'vue'

import { auth as authApi } from '@/api'
import EmptyState from '@/components/EmptyState.vue'
import SearchField from '@/components/SearchField.vue'
import { useListFilter } from '@/composables/useListFilter'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import type { RouteLocationRaw } from 'vue-router'

import type { AuditEntry } from '@/types'

const $q = useQuasar()
const entries = ref<AuditEntry[]>([])
const { term, filtered, summary, isFiltering } = useListFilter(entries, (e) => [
  e.action,
  e.resource_type,
  e.resource_id,
  e.actor_email,
])
const allEntries = ref<AuditEntry[]>([])
const resourceType = ref<string | null>(null)
const action = ref<string | null>(null)
const loading = ref(false)

const resourceTypes = computed(() =>
  [...new Set(allEntries.value.map((e) => e.resource_type))].sort(),
)
const actions = computed(() => [...new Set(allEntries.value.map((e) => e.action))].sort())

/**
 * Where an audited resource can be opened.
 *
 * Only the types with a destination are linked: a dangling link that lands on a
 * redirect is worse than plain text, because it promises something.
 */
function routeFor(entry: AuditEntry): RouteLocationRaw | null {
  const id = entry.resource_id
  if (!id) return null
  switch (entry.resource_type) {
    case 'dataset':
      return { name: 'dataset-detail', params: { id } }
    case 'model':
      return { name: 'model-detail', params: { id } }
    case 'execution':
      return { name: 'execution-detail', params: { id } }
    case 'pipeline':
      return { name: 'pipelines', query: { id } }
    case 'report':
      return { name: 'reports', query: { id } }
    case 'dashboard':
      return { name: 'dashboards', query: { id } }
    case 'experiment':
      return { name: 'experiments', query: { id } }
    default:
      return null
  }
}

function summarise(detail: Record<string, unknown>) {
  return Object.entries(detail)
    .slice(0, 3)
    .map(([key, value]) => `${key}=${typeof value === 'object' ? JSON.stringify(value) : value}`)
    .join(' ')
}

async function load() {
  loading.value = true
  try {
    const params = new URLSearchParams({ limit: '200' })
    if (resourceType.value) params.set('resource_type', resourceType.value)
    if (action.value) params.set('action', action.value)
    entries.value = await authApi.audit(`?${params}`)
    if (!resourceType.value && !action.value) allEntries.value = entries.value
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>
