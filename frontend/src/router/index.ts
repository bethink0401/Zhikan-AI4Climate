import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/',
    redirect: '/overview'
  },
  {
    path: '/overview',
    name: 'Overview',
    component: () => import('@/views/OverviewView.vue'),
    meta: { title: '综合态势' }
  },
  {
    path: '/restriction',
    name: 'Restriction',
    component: () => import('@/views/RestrictionWarningView.vue'),
    meta: { title: '污染溯源与区域交通治理' }
  },
  {
    path: '/weather',
    name: 'Weather',
    component: () => import('@/views/WeatherPropagationView.vue'),
    meta: { title: '公里级传播预警' }
  },
  {
    path: '/travel',
    name: 'Travel',
    component: () => import('@/views/TravelAdviceView.vue'),
    meta: { title: '出行建议与路线推荐' }
  }
]

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes
})

export default router
