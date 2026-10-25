<template>
  <section class="chat-route-map">
    <div class="map-heading">{{ map.title }}</div>
    <div class="map-frame">
      <TravelRouteMap
        :routes="map.routes"
        :origin="map.origin"
        :destination="map.destination"
        :selected-id="map.selected_route_id"
      />
    </div>
    <div class="map-note">路线与起终点来自本次服务端出行规划；地图底图由腾讯地图提供。</div>
  </section>
</template>

<script setup lang="ts">
import TravelRouteMap from '@/components/travel/TravelRouteMap.vue'
import type { ChatRouteMapData } from '@/types/aiChat'
defineProps<{ map: ChatRouteMapData }>()
</script>

<style scoped>
.chat-route-map {
  width: 100%;
  max-width: 960px;
  margin: 16px auto 0;
  padding: 14px;
  box-sizing: border-box;
  background: #fff;
  border: 1px solid var(--border-color);
  border-radius: 8px;
}
.map-heading { font-size: 13px; font-weight: 600; margin-bottom: 8px; }
.map-frame { height: 460px; overflow: hidden; border-radius: 8px; }
.map-note { color: var(--text-tertiary); font-size: 11px; margin-top: 7px; }
@media (max-width: 700px) { .map-frame { height: 340px; } }
</style>
