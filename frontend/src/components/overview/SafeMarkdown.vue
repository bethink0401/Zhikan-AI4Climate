<template>
  <div class="safe-markdown" v-html="rendered"></div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{ content: string }>()

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;')
}

function inlineMarkdown(value: string): string {
  return escapeHtml(value)
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\[(\d+)\]/g, '<span class="citation">[$1]</span>')
}

function renderMarkdown(value: string): string {
  const cleaned = value
    .replace(/<svg\b[\s\S]*?<\/svg>/gi, '')
    .replace(/(?:^|\s)_svg_(?=\s|$)/gi, ' ')
    .replace(/\\([*_#[\]`])/g, '$1')
    .trim()
  const lines = cleaned.split(/\r?\n/)
  const output: string[] = []
  let listType: 'ul' | 'ol' | null = null
  const closeList = () => {
    if (listType) output.push(`</${listType}>`)
    listType = null
  }
  for (const raw of lines) {
    const line = raw.trim()
    if (!line) {
      closeList()
      continue
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line)
    if (heading) {
      closeList()
      const level = Math.min(heading[1].length + 2, 5)
      output.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`)
      continue
    }
    const bullet = /^[-*•]\s+(.+)$/.exec(line)
    const numbered = /^\d+[.)、]\s*(.+)$/.exec(line)
    if (bullet || numbered) {
      const nextType = bullet ? 'ul' : 'ol'
      if (listType !== nextType) {
        closeList()
        listType = nextType
        output.push(`<${nextType}>`)
      }
      output.push(`<li>${inlineMarkdown((bullet ?? numbered)![1])}</li>`)
      continue
    }
    closeList()
    output.push(`<p>${inlineMarkdown(line)}</p>`)
  }
  closeList()
  return output.join('')
}

const rendered = computed(() => renderMarkdown(props.content || ''))
</script>

<style scoped>
.safe-markdown { color: inherit; font-size: inherit; line-height: 1.65; overflow-wrap: anywhere; }
.safe-markdown :deep(p) { margin: 0 0 7px; }
.safe-markdown :deep(p:last-child) { margin-bottom: 0; }
.safe-markdown :deep(h3), .safe-markdown :deep(h4), .safe-markdown :deep(h5) { margin: 9px 0 5px; color: var(--text-primary); font-size: 12px; line-height: 1.4; }
.safe-markdown :deep(ul), .safe-markdown :deep(ol) { margin: 5px 0 7px; padding-left: 20px; }
.safe-markdown :deep(li + li) { margin-top: 4px; }
.safe-markdown :deep(strong) { color: var(--text-primary); font-weight: 650; }
.safe-markdown :deep(code) { padding: 1px 4px; border-radius: 3px; background: rgba(148, 163, 184, .13); font-size: .92em; }
.safe-markdown :deep(.citation) { color: #2563eb; font-weight: 600; }
</style>
