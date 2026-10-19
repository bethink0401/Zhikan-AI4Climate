<template>
  <el-drawer
    v-model="visible"
    title="Agent 运行状态"
    :size="480"
    direction="rtl"
  >
    <AgentProcess :agents="agentData" />
  </el-drawer>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import AgentProcess from './AgentProcess.vue'
import { useAnalysisStore } from '@/stores/analysis'
import type { AgentNode, AgentStatus } from './AgentProcess.vue'

const analysisStore = useAnalysisStore()

// 控制显示/隐藏
const visible = defineModel<boolean>('visible', { default: false })

// Agent 数据映射
const agentData = computed<AgentNode[]>(() => {
  const result = analysisStore.result
  if (!result) return []

  const agents: AgentNode[] = [
    {
      name: 'AirQualityAgent',
      label: '空气质量分析',
      status: mapAgentStatus(result.air_quality?.status),
      confidence: result.air_quality?.confidence,
      evidence: result.air_quality?.evidence?.slice(0, 2)
    },
    {
      name: 'MeteorologyAgent',
      label: '气象分析',
      status: mapAgentStatus(result.meteorology?.status),
      confidence: result.meteorology?.confidence,
      evidence: result.meteorology?.evidence?.slice(0, 2)
    },
    {
      name: 'TrafficAgent',
      label: '交通分析',
      status: mapAgentStatus(result.traffic?.status),
      confidence: result.traffic?.confidence,
      evidence: result.traffic?.evidence?.slice(0, 2)
    },
    {
      name: 'SpatialAgent',
      label: '空间分析',
      status: mapAgentStatus(result.spatial?.status),
      confidence: result.spatial?.confidence,
      evidence: result.spatial?.evidence?.slice(0, 2)
    },
    {
      name: 'SourceAgent',
      label: '污染溯源',
      status: mapAgentStatus(result.source?.status),
      confidence: result.source?.confidence,
      evidence: result.source?.evidence?.slice(0, 2)
    },
    {
      name: 'DecisionAgent',
      label: '决策建议',
      status: mapAgentStatus(result.decision?.status),
      confidence: result.decision?.confidence,
      evidence: result.decision?.evidence?.slice(0, 2)
    },
    {
      name: 'ReportAgent',
      label: '报告生成',
      status: mapAgentStatus(result.report?.status),
      confidence: result.report?.confidence,
      evidence: result.report?.evidence?.slice(0, 2)
    }
  ]

  return agents
})

function mapAgentStatus(status?: string): AgentStatus {
  if (!status) return 'pending'
  const statusMap: Record<string, AgentStatus> = {
    success: 'success',
    warning: 'warning',
    failed: 'error',
    error: 'error'
  }
  return statusMap[status] || 'pending'
}
</script>

<style scoped>
/* Drawer 内容样式由 AgentProcess 组件自身控制 */
</style>
