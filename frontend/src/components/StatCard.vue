<template>
  <q-card flat class="flux-card">
    <q-card-section class="q-pb-xs">
      <div class="row items-center q-gutter-xs">
        <q-icon v-if="icon" :name="icon" size="20px" class="text-primary" />
        <span class="text-caption text-uppercase text-weight-medium" style="opacity: 0.7">
          {{ label }}
        </span>
      </div>
      <div class="text-h5 text-weight-bold q-mt-xs">{{ value }}</div>
      <div v-if="hint" class="text-caption" style="opacity: 0.6">{{ hint }}</div>
    </q-card-section>
  </q-card>
</template>

<script setup lang="ts">
defineProps<{ label: string; value: string | number; hint?: string; icon?: string }>()
</script>
