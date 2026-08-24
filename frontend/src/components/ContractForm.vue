<template>
  <div class="fx-contract">
    <div v-for="field in visibleFields" :key="field.name" class="fx-contract__field">
      <!--
        A field that contains other fields renders as a group of them. The
        component calls itself, which is what lets one contract describe a
        constraint once and have it look the same wherever it appears.
      -->
      <fieldset v-if="field.fields && field.fields.length" class="fx-contract__group">
        <legend>{{ labelFor(field) }}</legend>
        <p v-if="field.description" class="fx-meta">{{ field.description }}</p>
        <ContractForm
          :fields="field.fields"
          :model-value="asObject(field)"
          :columns="columns"
          @update:model-value="(value) => set(field, value)"
        />
      </fieldset>

      <!-- a list: as many of the same shape as you need -->
      <fieldset v-else-if="field.item" class="fx-contract__group">
        <legend>{{ labelFor(field) }}</legend>
        <p v-if="field.description" class="fx-meta">{{ field.description }}</p>
        <div
          v-for="(entry, index) in asList(field)"
          :key="index"
          class="fx-contract__entry"
        >
          <div class="fx-contract__entry-head">
            <span class="fx-meta">{{ index + 1 }}</span>
            <q-btn
              flat dense round icon="close" size="sm"
              :aria-label="`remove ${field.name} ${index + 1}`"
              @click="removeFrom(field, index)"
            />
          </div>
          <ContractForm
            v-if="field.item.fields && field.item.fields.length"
            :fields="field.item.fields"
            :model-value="(entry as Record<string, unknown>) || {}"
            :columns="columns"
            @update:model-value="(value) => replaceIn(field, index, value)"
          />
          <q-input
            v-else
            :model-value="String(entry ?? '')"
            :label="field.item.name"
            dense outlined
            @update:model-value="(value) => replaceIn(field, index, value)"
          />
        </div>
        <q-btn
          flat dense no-caps icon="add" :label="`Add ${field.item.name}`"
          @click="appendTo(field)"
        />
      </fieldset>

      <!-- a mapping: keys you name, values that all look alike -->
      <fieldset v-else-if="field.values" class="fx-contract__group">
        <legend>{{ labelFor(field) }}</legend>
        <p v-if="field.description" class="fx-meta">{{ field.description }}</p>
        <div
          v-for="key in Object.keys(asObject(field))"
          :key="key"
          class="fx-contract__entry"
        >
          <div class="fx-contract__entry-head">
            <span class="mono">{{ key }}</span>
            <q-btn
              flat dense round icon="close" size="sm"
              :aria-label="`remove ${key}`"
              @click="removeKey(field, key)"
            />
          </div>
          <ContractForm
            v-if="field.values.fields && field.values.fields.length"
            :fields="field.values.fields"
            :model-value="(asObject(field)[key] as Record<string, unknown>) || {}"
            :columns="columns"
            @update:model-value="(value) => setKey(field, key, value)"
          />
        </div>
        <div class="fx-contract__add">
          <q-input
            v-model="newKeys[field.name]"
            :label="`New ${field.values.name} name`"
            dense outlined
            @keyup.enter="addKey(field)"
          />
          <q-btn flat dense no-caps icon="add" label="Add" @click="addKey(field)" />
        </div>
      </fieldset>

      <!-- an enum is a choice, not a typing exercise -->
      <q-select
        v-else-if="field.enum && field.enum.length"
        :model-value="asText(field)"
        :options="field.enum"
        :label="labelFor(field)"
        :hint="hintFor(field)"
        dense
        outlined
        :clearable="!field.required"
        @update:model-value="(value) => set(field, value)"
      />

      <q-toggle
        v-else-if="field.type === 'boolean'"
        :model-value="asBoolean(field)"
        :label="labelFor(field)"
        dense
        @update:model-value="(value) => set(field, value)"
      />

      <q-input
        v-else-if="field.type === 'integer' || field.type === 'float'"
        :model-value="asText(field)"
        :label="labelFor(field)"
        :hint="hintFor(field)"
        type="number"
        dense
        outlined
        @update:model-value="(value) => setNumber(field, value)"
      />

      <!-- lists and objects are typed as text, then parsed on the way out -->
      <q-input
        v-else-if="field.type === 'array' || field.type === 'json'"
        :model-value="asText(field)"
        :label="labelFor(field)"
        :hint="hintFor(field)"
        :error="!!errors[field.name]"
        :error-message="errors[field.name]"
        type="textarea"
        autogrow
        rows="2"
        dense
        outlined
        class="mono"
        @update:model-value="(value) => setStructured(field, value)"
      />

      <q-select
        v-else-if="columns.length"
        :model-value="asText(field)"
        :options="columns"
        :label="labelFor(field)"
        :hint="hintFor(field)"
        dense
        outlined
        use-input
        new-value-mode="add-unique"
        :clearable="!field.required"
        @update:model-value="(value) => set(field, value)"
        @filter="filterColumns"
      />

      <q-input
        v-else
        :model-value="asText(field)"
        :label="labelFor(field)"
        :hint="hintFor(field)"
        dense
        outlined
        @update:model-value="(value) => set(field, value)"
      />
    </div>

    <div v-if="!fields.length" class="fx-meta">This step takes no parameters.</div>
  </div>
</template>

<script setup lang="ts">
import { computed, reactive, ref } from 'vue'

import type { FieldSpec } from '@/types'

//  Named so the template can call itself. A contract describes a shape once
//  and it renders the same wherever it appears, however deeply.
defineOptions({ name: 'ContractForm' })

/**
 * Renders a Contract as a form, so configuring a step never means hand-writing
 * JSON. The contract already states each parameter's type, whether it is
 * required, its default and its allowed values — everything a form needs.
 *
 * Values are emitted as the plain object the API expects: lists and objects are
 * parsed from text here rather than being pushed onto the caller.
 */
const props = withDefaults(
  defineProps<{
    fields: FieldSpec[]
    modelValue: Record<string, unknown>
    /** Column names from the step's input, offered as suggestions. */
    columns?: string[]
  }>(),
  { columns: () => [] },
)

const emit = defineEmits<{ 'update:modelValue': [Record<string, unknown>] }>()

const errors = reactive<Record<string, string>>({})
const columnFilter = ref('')
//  One in-progress key name per mapping field, so two mappings on the same
//  form do not share a text box.
const newKeys = reactive<Record<string, string>>({})

/**
 * The fields that apply, given what has been filled in so far.
 *
 * A provider may say a field is only relevant in some configurations - a
 * polynomial's degree, a bounded constraint's upper limit. Showing all of them
 * at once leaves the reader to work out which ones the provider will actually
 * read, which is the job the contract exists to do.
 */
const visibleFields = computed(() => {
  //  A field's default is its value until somebody changes it, so a condition
  //  on "kind == range" has to hold for a record that never mentions `kind`
  //  and whose `kind` defaults to range. Reading the raw model value instead
  //  hid every dependent field on any example that relied on a default.
  const effective: Record<string, unknown> = { ...props.modelValue }
  for (const field of props.fields) {
    if (effective[field.name] === undefined || effective[field.name] === null) {
      if (field.default !== null && field.default !== undefined) {
        effective[field.name] = field.default
      }
    }
  }
  return props.fields.filter((field) => {
    const rule = field.visible_when
    if (!rule) return true
    const value = effective[rule.field]
    if (rule.in) return rule.in.includes(value)
    return value === rule.equals
  })
})

function asObject(field: FieldSpec): Record<string, unknown> {
  const value = props.modelValue[field.name]
  return value && typeof value === 'object' && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {}
}

function asList(field: FieldSpec): unknown[] {
  const value = props.modelValue[field.name]
  return Array.isArray(value) ? value : []
}

function appendTo(field: FieldSpec) {
  //  A new entry starts from the shape's defaults rather than empty, so the
  //  common case is already filled in.
  const blank: Record<string, unknown> = {}
  for (const inner of field.item?.fields ?? []) {
    if (inner.default !== null && inner.default !== undefined) blank[inner.name] = inner.default
  }
  set(field, [...asList(field), field.item?.fields?.length ? blank : ''])
}

function replaceIn(field: FieldSpec, index: number, value: unknown) {
  const next = [...asList(field)]
  next[index] = value
  set(field, next)
}

function removeFrom(field: FieldSpec, index: number) {
  set(field, asList(field).filter((_, i) => i !== index))
}

function addKey(field: FieldSpec) {
  const key = (newKeys[field.name] || '').trim()
  if (!key) return
  const blank: Record<string, unknown> = {}
  for (const inner of field.values?.fields ?? []) {
    if (inner.default !== null && inner.default !== undefined) blank[inner.name] = inner.default
  }
  set(field, { ...asObject(field), [key]: blank })
  newKeys[field.name] = ''
}

function setKey(field: FieldSpec, key: string, value: unknown) {
  set(field, { ...asObject(field), [key]: value })
}

function removeKey(field: FieldSpec, key: string) {
  const next = { ...asObject(field) }
  delete next[key]
  set(field, next)
}

const columns = computed(() => {
  const needle = columnFilter.value.toLowerCase()
  const all = props.columns
  return needle ? all.filter((name) => name.toLowerCase().includes(needle)) : all
})

function filterColumns(value: string, update: (fn: () => void) => void) {
  update(() => {
    columnFilter.value = value
  })
}

function labelFor(field: FieldSpec) {
  const name = field.name.replace(/_/g, ' ')
  return field.required ? `${name} *` : name
}

function hintFor(field: FieldSpec) {
  const parts: string[] = []
  if (field.description) parts.push(field.description)
  if (field.type === 'array') parts.push('comma-separated, or a JSON list')
  if (field.type === 'json') parts.push('JSON object, e.g. {"a": "b"}')
  if (field.default !== null && field.default !== undefined) {
    parts.push(`default ${JSON.stringify(field.default)}`)
  }
  return parts.join(' · ')
}

function asText(field: FieldSpec) {
  const value = props.modelValue[field.name]
  if (value === null || value === undefined) return ''
  if (typeof value === 'object') return JSON.stringify(value, null, 0)
  return String(value)
}

function asBoolean(field: FieldSpec) {
  const value = props.modelValue[field.name]
  return value === undefined ? Boolean(field.default) : Boolean(value)
}

function commit(name: string, value: unknown) {
  const next = { ...props.modelValue }
  if (value === null || value === undefined || value === '') delete next[name]
  else next[name] = value
  emit('update:modelValue', next)
}

function set(field: FieldSpec, value: unknown) {
  commit(field.name, value)
}

function setNumber(field: FieldSpec, value: string | number | null) {
  if (value === null || value === '') return commit(field.name, null)
  const parsed = Number(value)
  commit(field.name, Number.isNaN(parsed) ? null : parsed)
}

/**
 * A list can be written either way — `a, b, c` or `["a","b","c"]` — because
 * both are natural depending on whether the values are words or numbers.
 */
function setStructured(field: FieldSpec, raw: string | number | null) {
  const text = String(raw ?? '').trim()
  delete errors[field.name]
  if (!text) return commit(field.name, null)

  if (text.startsWith('{') || text.startsWith('[')) {
    try {
      commit(field.name, JSON.parse(text))
    } catch {
      errors[field.name] = 'not valid JSON yet'
    }
    return
  }

  if (field.type === 'json') {
    errors[field.name] = 'this parameter needs a JSON object'
    return
  }

  const parts = text
    .split(',')
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => (part !== '' && !Number.isNaN(Number(part)) ? Number(part) : part))
  commit(field.name, parts)
}
</script>

<style scoped>
/*
  A nested shape is shown as a bordered group so the eye can see where one
  constraint ends and the next begins. Without it a list of five rules reads as
  one long column of unrelated inputs.
*/
.fx-contract__group {
  border: 1px solid var(--fx-border);
  border-radius: var(--fx-radius-sm);
  padding: var(--fx-space-3);
  margin: 0;
  display: grid;
  gap: var(--fx-space-3);
}

.fx-contract__group > legend {
  font-size: var(--fx-text-xs);
  font-weight: 600;
  letter-spacing: 0.03em;
  padding: 0 var(--fx-space-1);
}

.fx-contract__entry {
  border-left: 2px solid var(--fx-border);
  padding-left: var(--fx-space-3);
  display: grid;
  gap: var(--fx-space-2);
}

.fx-contract__entry-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--fx-space-2);
}

.fx-contract__add {
  display: flex;
  align-items: flex-start;
  gap: var(--fx-space-2);
}

.fx-contract__add .q-field {
  flex: 1 1 auto;
}

.fx-contract {
  display: grid;
  gap: var(--fx-space-3);
}
</style>
