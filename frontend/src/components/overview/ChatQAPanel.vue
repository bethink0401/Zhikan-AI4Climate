<template>
  <AIChatPanel @focus-section="emit('focus-section', $event)" />
</template>

<script setup lang="ts">
import AIChatPanel from './AIChatPanel.vue'

defineOptions({ name: 'ChatQAPanel' })
const emit = defineEmits<{ (e: 'focus-section', topic: string): void }>()
</script>
