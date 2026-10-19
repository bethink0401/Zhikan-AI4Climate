<template>
  <div class="main-layout">
    <!-- 顶部导航栏 -->
    <header class="layout-header">
      <div class="header-left">
        <h1 class="system-title">智瞰空气质量智能分析平台</h1>
      </div>
      
      <nav class="header-nav">
        <router-link 
          v-for="item in navItems" 
          :key="item.path"
          :to="item.path"
          class="nav-item"
          active-class="nav-item-active"
        >
          {{ item.label }}
        </router-link>
      </nav>

      <div class="header-actions">
        <el-button @click="handleReport" type="primary">
          智能分析报告
        </el-button>
        <el-button @click="showAgentDrawer = true">
          Agent运行状态
        </el-button>
      </div>
    </header>

    <!-- 内容区域 -->
    <main class="layout-content">
      <slot />
    </main>

    <!-- Agent 状态 Drawer -->
    <AgentStatusDrawer v-model:visible="showAgentDrawer" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import AgentStatusDrawer from '@/components/global/AgentStatusDrawer.vue'

const navItems = [
  { path: '/overview', label: '综合态势' },
  { path: '/restriction', label: '污染溯源与区域交通治理' },
  { path: '/weather', label: '恶劣天气与公里级传播预警' },
  { path: '/travel', label: '出行建议与路线推荐' }
]

const showAgentDrawer = ref(false)

const handleReport = () => {
  ElMessage.info('智能分析报告功能开发中')
}
</script>

<style scoped>
.main-layout {
  width: 100%;
  height: 100vh;
  display: flex;
  flex-direction: column;
}

.layout-header {
  height: 60px;
  background: var(--panel-bg);
  border-bottom: 1px solid var(--border-color);
  display: flex;
  align-items: center;
  padding: 0 24px;
  gap: 24px;
}

.header-left {
  flex-shrink: 0;
}

.system-title {
  font-size: 20px;
  font-weight: 600;
  color: var(--text-primary);
  margin: 0;
}

.header-nav {
  flex: 1;
  display: flex;
  gap: 8px;
  margin-left: 48px;
}

.nav-item {
  padding: 8px 16px;
  color: var(--text-secondary);
  text-decoration: none;
  border-radius: 4px;
  transition: all 0.3s;
}

.nav-item:hover {
  background: var(--bg-page);
  color: var(--text-primary);
}

.nav-item-active {
  background: var(--theme-color);
  color: #fff;
}

.header-actions {
  display: flex;
  gap: 12px;
  flex-shrink: 0;
}

.layout-content {
  flex: 1;
  overflow: auto;
  background: var(--bg-page);
}
</style>
