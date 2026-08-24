<template>
  <q-page class="page-shell">
    <PageHeader title="Settings" subtitle="What this deployment supports, read from the running backend" />

    <div v-if="info" class="row q-col-gutter-md">
      <div class="col-12 col-md-6">
        <SectionCard title="Platform" subtitle="How this instance is configured right now" class="q-mb-md">
          <FactList :facts="platformFacts" />
        </SectionCard>

        <SectionCard
          title="Model providers"
          :subtitle="`${info.providers.length} registered · ${trainableCount} trainable`"
          flush
        >
          <q-list dense separator class="fx-list">
            <q-item v-for="provider in info.providers" :key="provider.key">
              <q-item-section>
                <q-item-label>{{ provider.name }}</q-item-label>
                <q-item-label caption class="mono">{{ provider.key }}</q-item-label>
              </q-item-section>
              <q-item-section side>
                <div class="fx-tags">
                  <span class="fx-tag">{{ provider.model_type.replace(/_/g, ' ') }}</span>
                  <span v-if="provider.trainable" class="fx-tag">trainable</span>
                </div>
              </q-item-section>
            </q-item>
          </q-list>
        </SectionCard>
      </div>

      <div class="col-12 col-md-6">
        <SectionCard
          v-for="group in capabilityGroups"
          :key="group.label"
          :title="group.label"
          :subtitle="`${group.values.length} supported`"
          class="q-mb-md"
          tight
        >
          <div class="fx-tags">
            <span v-for="value in group.values" :key="value" class="fx-tag fx-tag--code">{{ value }}</span>
          </div>
        </SectionCard>
      </div>
    </div>
  </q-page>
</template>

<script setup lang="ts">
import { useQuasar } from 'quasar'
import { computed, onMounted, ref } from 'vue'

import { platform } from '@/api'
import FactList, { type Fact } from '@/components/FactList.vue'
import PageHeader from '@/components/PageHeader.vue'
import SectionCard from '@/components/SectionCard.vue'
import type { PlatformInfo } from '@/types'

const $q = useQuasar()
const info = ref<PlatformInfo | null>(null)
const metrics = ref<{
  uptime_seconds: number
  requests_total: number
  requests_failed_total: number
} | null>(null)

function formatUptime(seconds: number) {
  if (seconds < 60) return `${Math.round(seconds)}s`
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`
  return `${(seconds / 3600).toFixed(1)}h`
}

const trainableCount = computed(() => info.value?.providers.filter((p) => p.trainable).length ?? 0)

const platformFacts = computed<Fact[]>(() => {
  if (!info.value) return []
  const facts: Fact[] = [
    { label: 'Name', value: info.value.name },
    { label: 'Abstraction', value: info.value.abstraction },
    { label: 'Execution mode', value: info.value.execution_mode },
    { label: 'Storage backend', value: info.value.storage_backend },
    { label: 'Authentication', value: info.value.auth_enabled ? 'enabled' : 'disabled' },
    { label: 'Scheduler', value: info.value.scheduler_enabled ? 'running' : 'off' },
  ]
  if (metrics.value) {
    facts.push(
      {
        label: 'Requests served',
        value: `${metrics.value.requests_total.toLocaleString()} (${metrics.value.requests_failed_total} failed)`,
        numeric: true,
      },
      { label: 'Uptime', value: formatUptime(metrics.value.uptime_seconds), numeric: true },
    )
  }
  return facts
})

const capabilityGroups = computed(() => [
  { label: 'Source types', values: info.value?.source_types ?? [] },
  { label: 'Model types', values: info.value?.model_types ?? [] },
  { label: 'Runtimes', values: info.value?.runtimes ?? [] },
  { label: 'Execution kinds', values: info.value?.execution_kinds ?? [] },
  { label: 'Result kinds', values: info.value?.result_kinds ?? [] },
])

onMounted(async () => {
  try {
    info.value = await platform.info()
    metrics.value = await platform.metricsSummary()
  } catch (error) {
    $q.notify({ type: 'negative', message: (error as Error).message })
  }
})
</script>
