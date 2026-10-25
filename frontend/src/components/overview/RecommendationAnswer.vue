<template>
  <section class="recommendation-answer" :aria-label="display.title">
    <h3>{{ display.title }}</h3>
    <div class="advice-list">
      <div v-for="(item, index) in display.direct_advice" :key="`${item.title}-${index}`" class="advice-item">
        <strong class="advice-title">{{ item.title }}</strong>
        <SafeMarkdown class="advice-detail" :content="item.detail" />
      </div>
    </div>
    <div class="reason-section">
      <h4>{{ display.direct_advice.length ? '为什么这样建议' : '生成状态' }}</h4>
      <SafeMarkdown :content="display.explanation" />
    </div>
  </section>
</template>

<script setup lang="ts">
import SafeMarkdown from './SafeMarkdown.vue'
import type { RecommendationDisplay } from '@/types/aiChat'

defineProps<{ display: RecommendationDisplay }>()
</script>

<style scoped>
.recommendation-answer { min-width: 0; text-align: left; }
.recommendation-answer h3 { margin: 0 0 10px; color: var(--text-primary); font-size: 14px; font-weight: 700; }
.advice-list { display: grid; grid-template-columns: minmax(0, 1fr); }
.advice-item { display: grid; grid-template-columns: minmax(0, 1fr); gap: 4px; padding: 12px 0; border-bottom: 1px solid rgba(148, 163, 184, .16); font-size: 12px; line-height: 1.75; }
.advice-item:first-child { padding-top: 4px; }
.advice-item:last-child { border-bottom: 0; }
.advice-title { color: #1d4ed8; font-weight: 650; overflow-wrap: anywhere; }
.advice-detail { color: var(--text-secondary); min-width: 0; overflow-wrap: anywhere; }
.advice-detail :deep(p) { margin: 0; }
.reason-section { margin-top: 10px; padding-top: 12px; border-top: 1px solid rgba(148, 163, 184, .18); color: var(--text-secondary); font-size: 12px; line-height: 1.75; }
.reason-section h4 { margin: 0 0 5px; color: var(--text-primary); font-size: 12px; font-weight: 650; }
.reason-section :deep(p) { margin: 0; }
</style>
