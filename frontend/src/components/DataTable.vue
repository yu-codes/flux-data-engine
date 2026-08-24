<template>
  <q-table
    flat
    dense
    :rows="rows"
    :columns="columns"
    :row-key="rowKey"
    :loading="loading"
    :pagination="{ rowsPerPage }"
    :rows-per-page-options="[10, 25, 50, 100]"
    class="fx-data-table"
  >
    <template #body-cell="cell">
      <q-td :props="cell">
        <span class="fx-cell" :class="{ mono: isMono(cell.value) }" :title="display(cell.value)">
          {{ display(cell.value) }}
        </span>
      </q-td>
    </template>
    <template #no-data>
      <div class="empty-state full-width">{{ emptyMessage }}</div>
    </template>
  </q-table>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = withDefaults(
  defineProps<{
    rows: Record<string, unknown>[]
    /** Column names in display order; inferred from the first row when omitted. */
    fields?: { name: string; type?: string }[]
    rowKey?: string
    loading?: boolean
    rowsPerPage?: number
    emptyMessage?: string
  }>(),
  { rowKey: 'id', loading: false, rowsPerPage: 25, emptyMessage: 'Nothing here yet.' },
)

const NUMERIC = new Set(['integer', 'float'])

const columns = computed(() => {
  const names: { name: string; type?: string }[] = props.fields?.length
    ? props.fields
    : Object.keys(props.rows[0] ?? {}).map((name) => ({ name }))
  return names.map((field) => ({
    name: field.name,
    label: field.name,
    field: field.name,
    align: (NUMERIC.has(field.type ?? '') ? 'right' : 'left') as 'left' | 'right',
    sortable: true,
  }))
})

function display(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'object') return JSON.stringify(value)
  if (typeof value === 'number') {
    return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(4)))
  }
  return String(value)
}

function isMono(value: unknown): boolean {
  return typeof value === 'object' || (typeof value === 'string' && /^[a-z]+_[0-9a-f]{8,}$/.test(value))
}
</script>

<style scoped>
/*
 * A wide table scrolls; it does not wrap. Sixty columns of a typhoon record
 * wrapped to a phone's width produced a page 190,000 pixels tall — unreadable,
 * and not a table any more. Cells keep to one line, the table takes the width
 * it needs, and q-table's own container does the scrolling.
 */
.fx-data-table :deep(th),
.fx-data-table :deep(td) {
  white-space: nowrap;
}

/* One very long value must not set the width of the whole table. */
.fx-cell {
  display: inline-block;
  max-width: 34ch;
  overflow: hidden;
  text-overflow: ellipsis;
  vertical-align: bottom;
}
</style>
