<template>
  <section class="chat-propagation-map">
    <div class="map-heading">{{ map.title }}</div>
    <div class="map-scope">{{ map.station_name }} · {{ map.start_time }} 至 {{ map.end_time }}</div>
    <div class="map-frame">
      <SpatialMap :spatial="map.spatial" :meteorology="map.meteorology" show-forecast />
    </div>
    <div class="map-note">黄色烟羽为本次分析的传播模拟，非实测污染轨迹；可平移缩放，并点击沿途站点查看观测对照。</div>
  </section>
</template>

<script setup lang="ts">
import SpatialMap from '@/components/spatial/SpatialMap.vue'
import type { ChatPropagationMapData } from '@/types/aiChat'

defineProps<{ map: ChatPropagationMapData }>()
</script>

<style scoped>
.chat-propagation-map { width: 100%; max-width: 960px; margin: 16px auto 0; padding: 14px; box-sizing: border-box; border: 1px solid var(--border-color); border-radius: 8px; background: #fff; }
.map-heading { font-size: 13px; font-weight: 600; margin-bottom: 6px; }
.map-scope, .map-note { font-size: 11px; color: var(--text-tertiary); line-height: 1.6; }
.map-scope { margin-bottom: 8px; }
.map-frame { height: 460px; overflow: hidden; border-radius: 8px; }
.map-note { margin-top: 7px; }
@media (max-width: 700px) { .map-frame { height: 420px; } }
</style>
