<template>
  <div>
    <div class="lineage__controls">
      <q-btn-toggle
        v-model="direction"
        :options="[
          { label: 'Where it came from', value: 'up' },
          { label: 'What depends on it', value: 'down' },
        ]"
        dense
        unelevated
        no-caps
        toggle-color="primary"
        @update:model-value="load"
      />
      <span v-if="graph?.truncated" class="fx-meta">
        showing {{ depth }} steps — there is more beyond this
      </span>
    </div>

    <AsyncSection :pending="loading" :error="error" title="Could not trace this">
      <EmptyState
        v-if="graph && graph.nodes.length < 2"
        :message="direction === 'up' ? 'Nothing upstream' : 'Nothing depends on this yet'"
        :hint="
          direction === 'up'
            ? 'This is where the trail starts.'
            : 'Nothing has read it so far.'
        "
        icon="account_tree"
      />

      <div v-else-if="graph" class="fx-scroll-x">
        <svg
          :viewBox="`0 0 ${width} ${height}`"
          :style="{ minWidth: `${width}px` }"
          class="lineage__svg"
          role="img"
          aria-label="Lineage graph"
        >
          <g>
            <path
              v-for="(edge, index) in laidOutEdges"
              :key="`edge-${index}`"
              :d="edge.path"
              class="lineage__edge"
            />
          </g>
          <g v-for="laid in laidOutNodes" :key="laid.node.key">
            <rect
              :x="laid.x"
              :y="laid.y"
              :width="NODE_W"
              :height="NODE_H"
              rx="7"
              class="lineage__node"
              :class="[
                `lineage__node--${laid.node.kind}`,
                laid.node.key === graph.root ? 'lineage__node--root' : '',
              ]"
            />
            <text
              :x="laid.x + NODE_W / 2"
              :y="laid.y + 20"
              text-anchor="middle"
              class="lineage__label"
            >
              {{ truncate(laid.node.label, 18) }}
            </text>
            <text
              :x="laid.x + NODE_W / 2"
              :y="laid.y + 36"
              text-anchor="middle"
              class="lineage__sublabel"
            >
              {{ laid.node.kind.replace('_', ' ') }}
            </text>
          </g>
        </svg>
      </div>
    </AsyncSection>
  </div>
</template>

<script setup lang="ts">
/**
 * Where a number came from, or what reads it.
 *
 * The platform recorded every edge of this and could not be asked about any of
 * them: the lineage dicts were write-only. This draws the answer using the
 * same layered arrangement as the pipeline graph, through the composable both
 * share, so the two pictures of "what flows into what" look like each other.
 */
import { computed, onMounted, ref } from 'vue'

import { lineage as lineageApi } from '@/api'
import AsyncSection from '@/components/AsyncSection.vue'
import EmptyState from '@/components/EmptyState.vue'
import { useLayeredGraph } from '@/composables/useLayeredGraph'
import type { LineageGraphData, LineageNode } from '@/types'

const props = withDefaults(
  defineProps<{ kind: string; nodeId: string; depth?: number }>(),
  { depth: 5 },
)

const graph = ref<LineageGraphData | null>(null)
const direction = ref<'up' | 'down'>('up')
const loading = ref(true)
const error = ref<string | null>(null)

const nodes = computed<LineageNode[]>(() => graph.value?.nodes ?? [])
const edges = computed(() => graph.value?.edges ?? [])
const { laidOutNodes, laidOutEdges, width, height, NODE_W, NODE_H } = useLayeredGraph(
  nodes,
  edges,
  (node) => node.key,
)

async function load() {
  loading.value = true
  error.value = null
  try {
    graph.value = await lineageApi.trace(props.kind, props.nodeId, {
      direction: direction.value,
      depth: props.depth,
    })
  } catch (caught) {
    error.value = (caught as Error).message
  } finally {
    loading.value = false
  }
}

function truncate(value: string, max: number) {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}

onMounted(load)
</script>

<style scoped>
.lineage__controls {
  display: flex;
  align-items: center;
  gap: var(--fx-space-3);
  flex-wrap: wrap;
  margin-bottom: var(--fx-space-3);
}

.lineage__svg {
  height: auto;
}

.lineage__edge {
  fill: none;
  stroke: var(--fx-line);
  stroke-width: 1.5;
}

.lineage__node {
  fill: var(--fx-surface);
  stroke: var(--fx-line);
}

/*  Kind carries the colour, so the shape of the chain is readable at a glance. */
.lineage__node--source,
.lineage__node--dataset,
.lineage__node--dataset_version {
  stroke: var(--fx-ok);
}

.lineage__node--model,
.lineage__node--execution {
  stroke: var(--fx-run);
}

.lineage__node--result,
.lineage__node--visualization,
.lineage__node--dashboard {
  stroke: var(--fx-wait);
}

/*  The thing that was asked about, so it can be found in its own graph. */
.lineage__node--root {
  stroke-width: 2.5;
}

.lineage__label {
  font-size: var(--fx-text-sm);
  fill: var(--fx-ink);
}

.lineage__sublabel {
  font-size: var(--fx-text-xs);
  fill: var(--fx-ink-soft);
}
</style>
