<template>
  <div class="weather-evidence">
    <!-- 标题 -->
    <div class="evidence-header">
      <span class="evidence-title">📋 气象证据</span>
      <span v-if="evidenceList.length > 0" class="evidence-count">
        {{ evidenceList.length }} 条
      </span>
    </div>

    <!-- 空状态 -->
    <div v-if="isEmpty" class="empty-state">
      <el-empty description="暂无气象证据" />
    </div>

    <!-- 证据列表 -->
    <ul v-else class="evidence-list">
      <li
        v-for="(item, index) in evidenceList"
        :key="index"
        class="evidence-item"
      >
        <span class="evidence-index">{{ index + 1 }}</span>
        <span class="evidence-text">{{ item }}</span>
      </li>
    </ul>
  </div>
</template>

<script setup lang="ts">
import { computed } from 'vue'

defineOptions({ name: 'WeatherEvidence' })

// ---------- 1. Props 类型定义 ----------
const props = defineProps<{
  /** 气象证据列表，如 ['连续低风速持续 4 小时', '相对湿度 >80% 持续 6 小时'] */
  evidence: string[]
}>()

// ---------- 2. 判断数据是否为空 ----------
const isEmpty = computed(() => {
  return !props.evidence || props.evidence.length === 0
})

// ---------- 3. 证据列表 ----------
const evidenceList = computed(() => {
  return props.evidence || []
})
</script>

<style scoped>
.weather-evidence {
  background: var(--panel-bg);
  border-radius: 8px;
  padding: 16px 20px;
  box-shadow: var(--shadow-sm);
  min-height: 120px;
}

/* ===== 标题区域 ===== */
.evidence-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--border-color);
  margin-bottom: 12px;
}

.evidence-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--text-primary);
}

.evidence-count {
  font-size: 12px;
  color: var(--text-tertiary);
  background: var(--bg-page);
  padding: 2px 10px;
  border-radius: 12px;
}

/* ===== 空状态 ===== */
.empty-state {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 80px;
}

/* ===== 证据列表 ===== */
.evidence-list {
  list-style: none;
  padding: 0;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.evidence-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 8px 12px;
  background: var(--bg-page);
  border-radius: 6px;
  transition: background 0.2s;
}

.evidence-item:hover {
  background: var(--border-color);
}

.evidence-index {
  flex-shrink: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  background: var(--theme-color);
  color: #fff;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 600;
  margin-top: 1px;
}

.evidence-text {
  font-size: 13px;
  color: var(--text-secondary);
  line-height: 1.6;
}
</style>