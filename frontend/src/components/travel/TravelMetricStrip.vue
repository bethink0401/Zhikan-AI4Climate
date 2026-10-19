<template>
  <section class="tf-metrics">
    <div
      v-for="item in items"
      :key="item.key"
      class="tf-metric"
      :class="item.tone ? `is-${item.tone}` : ''"
    >
      <span class="tf-metric-label">{{ item.label }}</span>
      <span class="tf-metric-value tf-mono">
        {{ item.value }}<em v-if="item.unit">{{ item.unit }}</em>
      </span>
    </div>
  </section>
</template>

<script setup lang="ts">
import type { TravelMetricItem } from '@/types/travel'

/**
 * TravelMetricStrip - 顶部气象与污染概况指标条
 *
 * 一行铺开 8 个指标，降雨 / 污染风险 / 暴露峰值用色调区分，
 * 让"哪个数是需要留意的"在一屏之内可辨。
 */

defineProps<{ items: TravelMetricItem[] }>()
</script>

<style scoped>
.tf-metrics {
  display: grid;
  grid-template-columns: repeat(8, minmax(0, 1fr));
  gap: 8px;
}

.tf-metric {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 3px;
  padding: 9px 8px;
  background: var(--tf-card);
  border: 1px solid var(--tf-border);
  border-radius: var(--tf-radius-sm);
  box-shadow: var(--tf-shadow);
}

.tf-metric-label {
  font-size: 12px;
  color: var(--tf-text-3);
}

.tf-metric-value {
  font-size: 18px;
  font-weight: 600;
  line-height: 1.2;
  color: var(--tf-text);
  white-space: nowrap;
}

.tf-metric-value em {
  font-style: normal;
  font-size: 12px;
  font-weight: 500;
  color: var(--tf-text-3);
  margin-left: 2px;
}

.tf-metric.is-blue { background: #f5f9ff; border-color: #e3edfb; }
.tf-metric.is-blue .tf-metric-value { color: #2f7fe0; }
.tf-metric.is-blue .tf-metric-value em { color: #6da2e6; }

.tf-metric.is-amber { background: #fffaf0; border-color: #f7ead2; }
.tf-metric.is-amber .tf-metric-value { color: #cf8a17; }
.tf-metric.is-amber .tf-metric-value em { color: #dba95b; }

.tf-metric.is-red { background: #fff5f6; border-color: #f9e0e4; }
.tf-metric.is-red .tf-metric-value { color: #dd4a5e; }
.tf-metric.is-red .tf-metric-value em { color: #e58b98; }

@media (max-width: 1280px) {
  .tf-metrics {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@media (max-width: 720px) {
  .tf-metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
