<template>
  <section v-if="knowledge?.summary || sources.length" class="knowledge-supplement">
    <h4>专业知识补充</h4>
    <SafeMarkdown v-if="knowledge?.summary" :content="knowledge.summary" />
    <div v-if="sources.length" class="references">
      <div class="references-title">参考来源</div>
      <div v-for="(source, index) in sources" :key="sourceKey(source, index)" class="reference-item">
        <span class="reference-index">[{{ index + 1 }}]</span>
        <span>{{ sourceLabel(source) }}</span>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import SafeMarkdown from './SafeMarkdown.vue'
import type { RecommendationDisplay } from '@/types/aiChat'

const props = defineProps<{ knowledge?: RecommendationDisplay['professional_knowledge'] }>()
const sources = computed(() => props.knowledge?.sources ?? [])

function sourceKey(source: Record<string, unknown>, index: number): string {
  return `${String(source.document_id ?? 'source')}-${String(source.page ?? source.page_start ?? index)}`
}

function sourceLabel(source: Record<string, unknown>): string {
  const parts = [source.title, source.author, source.year].filter(Boolean).map(String)
  if (source.page) parts.push(`p.${String(source.page)}`)
  else if (source.page_start) parts.push(`pp.${String(source.page_start)}-${String(source.page_end ?? source.page_start)}`)
  if (source.authority_level) parts.push(`权威等级：${String(source.authority_level)}`)
  if (source.citation_verified === false) parts.push('元数据由上传者提供，未做外部真实性核验')
  return parts.join('，')
}
</script>

<style scoped>
.knowledge-supplement { margin-top: 9px; padding: 9px 10px; border-radius: 7px; background: rgba(59, 130, 246, .055); color: var(--text-secondary); font-size: 11px; }
.knowledge-supplement h4 { margin: 0 0 5px; color: var(--text-primary); font-size: 12px; }
.references { margin-top: 8px; padding-top: 7px; border-top: 1px dashed rgba(148, 163, 184, .25); }
.references-title { margin-bottom: 4px; font-weight: 600; }
.reference-item { display: flex; gap: 5px; margin-top: 3px; line-height: 1.45; }
.reference-index { color: #2563eb; font-weight: 600; }
</style>
