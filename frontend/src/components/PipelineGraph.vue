<template>
  <div class="graph-host">
    <svg
      :viewBox="`0 0 ${width} ${height}`"
      :style="{ minWidth: `${width}px` }"
      class="graph-svg"
      role="img"
      aria-label="Pipeline graph"
    >
      <!-- edges first so nodes sit on top -->
      <g>
        <path
          v-for="(edge, index) in laidOutEdges"
          :key="`edge-${index}`"
          :d="edge.path"
          class="graph-edge"
        />
      </g>

      <g v-for="node in laidOutNodes" :key="node.id">
        <rect
          :x="node.x"
          :y="node.y"
          :width="NODE_W"
          :height="NODE_H"
          rx="7"
          class="graph-node"
          :class="[`graph-node--${node.type}`, node.state ? `graph-node--${node.state}` : '']"
        />
        <text :x="node.x + NODE_W / 2" :y="node.y + 20" text-anchor="middle" class="graph-label">
          {{ truncate(node.label, 18) }}
        </text>
        <text
          :x="node.x + NODE_W / 2"
          :y="node.y + 36"
          text-anchor="middle"
          class="graph-sublabel"
        >
          {{ truncate(node.sublabel, 22) }}
        </text>
      </g>
    </svg>

    <div class="row q-gutter-md q-mt-xs">
      <div v-for="legend in LEGEND" :key="legend.label" class="row items-center q-gutter-xs">
        <span class="legend-swatch" :class="legend.cls" />
        <span class="text-caption">{{ legend.label }}</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

import { useLayeredGraph } from '@/composables/useLayeredGraph'
import type { StepRun } from '@/types'

interface GraphNode {
  id: string
  label: string
  type: string
  model_name?: string
  kind?: string | null
}

const props = withDefaults(
  defineProps<{
    graph: { nodes: GraphNode[]; edges: { from: string; to: string }[]; terminal_steps: string[] }
    stepRuns?: StepRun[]
  }>(),
  { stepRuns: () => [] },
)

/**
 * Layered layout: depth from the input node decides the column, order within
 * the column decides the row. Enough for the tree shapes a pipeline can take,
 * and it needs no graph library.
 */
const NODE_W = 150
const NODE_H = 48
const GAP_X = 66
const GAP_Y = 22

const LEGEND = [
  { label: 'Dataset', cls: 'legend-swatch--dataset' },
  { label: 'Model step', cls: 'legend-swatch--model' },
  { label: 'Succeeded', cls: 'legend-swatch--succeeded' },
  { label: 'Failed', cls: 'legend-swatch--failed' },
]

const statusByStep = computed(() =>
  Object.fromEntries(props.stepRuns.map((step) => [step.step_name, step.status])),
)

//  The arrangement is shared with the lineage graph: same idea, same picture.
const graphNodes = computed(() => props.graph.nodes)
const graphEdges = computed(() => props.graph.edges)
const { laidOutNodes: placed, laidOutEdges, width, height } = useLayeredGraph(
  graphNodes,
  graphEdges,
  (node) => node.id,
  { nodeWidth: NODE_W, nodeHeight: NODE_H, gapX: GAP_X, gapY: GAP_Y },
)

const laidOutNodes = computed(() =>
  placed.value.map(({ node, x, y }) => ({
    ...node,
    sublabel:
      node.type === 'dataset'
        ? 'input dataset'
        //  A nested step is a pipeline of its own; saying only its name would
        //  make it look like any other step.
        : node.type === 'pipeline'
          ? `${node.model_name ?? ''} · pipeline`.trim()
          : (node.model_name ?? ''),
    state: node.type === 'dataset' ? '' : (statusByStep.value[node.id] ?? ''),
    x,
    y,
  })),
)

function truncate(value: string, max: number) {
  return value.length > max ? `${value.slice(0, max - 1)}…` : value
}
</script>

<style scoped>
.graph-host {
  width: 100%;
  overflow-x: auto;
}

/*
 * The graph draws at its natural size and scrolls when the column is narrower.
 * Scaling it down instead — which `width: 100%` did — turned a twelve-step
 * pipeline on a phone into a row of unreadable 28px smudges: a picture of a
 * diagram rather than a diagram.
 */
.graph-svg {
  height: auto;
  display: block;
}

.graph-edge {
  fill: none;
  stroke: currentColor;
  stroke-opacity: 0.35;
  stroke-width: 1.6;
}

.graph-node {
  fill: rgba(128, 145, 160, 0.12);
  stroke: rgba(128, 145, 160, 0.5);
  stroke-width: 1.2;
}

.graph-node--dataset {
  fill: rgba(47, 111, 143, 0.16);
  stroke: rgba(47, 111, 143, 0.7);
}

/*  A step that runs another pipeline, drawn as its own kind of thing: it is
    not a model, and a reader tracing a graph should not have to guess. */
.graph-node--pipeline {
  fill: rgba(120, 94, 160, 0.16);
  stroke: rgba(120, 94, 160, 0.7);
  stroke-dasharray: 4 2;
}

.graph-node--succeeded {
  fill: rgba(63, 125, 88, 0.18);
  stroke: rgba(63, 125, 88, 0.85);
}

.graph-node--failed {
  fill: rgba(179, 69, 59, 0.18);
  stroke: rgba(179, 69, 59, 0.85);
}

.graph-node--cancelled {
  stroke-dasharray: 5 3;
}

.graph-label {
  fill: currentColor;
  font-size: 12px;
  font-weight: 600;
}

.graph-sublabel {
  fill: currentColor;
  fill-opacity: 0.6;
  font-size: 10px;
}

.legend-swatch {
  width: 12px;
  height: 12px;
  border-radius: 3px;
  display: inline-block;
  border: 1px solid rgba(128, 145, 160, 0.6);
  background: rgba(128, 145, 160, 0.15);
}

.legend-swatch--dataset {
  background: rgba(47, 111, 143, 0.3);
  border-color: rgba(47, 111, 143, 0.8);
}

.legend-swatch--model {
  background: rgba(128, 145, 160, 0.2);
}

.legend-swatch--succeeded {
  background: rgba(63, 125, 88, 0.3);
  border-color: rgba(63, 125, 88, 0.9);
}

.legend-swatch--failed {
  background: rgba(179, 69, 59, 0.3);
  border-color: rgba(179, 69, 59, 0.9);
}
</style>
