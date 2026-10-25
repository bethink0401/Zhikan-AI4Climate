/**
 * 腾讯地图 GL JS 加载器。
 *
 * 为什么不用 MapLibre / demotiles 那一套：
 *   1. 合规——地图底图只能使用腾讯、高德、百度、天地图这类有资质的服务，
 *      境外 demo 瓦片源与 OpenStreetMap 直连都不可用。
 *   2. 可用性——原先用的 `demotiles.maplibre.org` 是世界级演示瓦片集，
 *      tilejson 里 `maxzoom` 只有 6，而本项目的页面都是 z12~z14 的香港街道级视野，
 *      超出范围后瓦片全部 404，地图只能显示一片空白。这一条是实测确认的。
 *
 * 密钥来源：环境变量 `VITE_TMAP_KEY`，写在 `frontend/.env.local`（不进版本控制）。
 * 申请入口：https://lbs.qq.com/dev/console/application/mine —— 创建应用后添加 Key，
 * Javascript API GL 不需要勾选任何产品，直接创建即可。
 */

/** 缺密钥时的提示，组件会据此渲染一个明确的引导面板而不是黑屏。 */
export const MAP_KEY_MISSING_MESSAGE =
  '未配置腾讯地图密钥：请在 frontend/.env.local 里设置 VITE_TMAP_KEY=' +
  '你申请的Key（申请入口 https://lbs.qq.com/dev/console/application/mine），' +
  '保存后重启开发服务器。'

const SDK_URL = 'https://map.qq.com/api/gljs?v=1.exp'

/** 读取环境变量里的密钥，并排除掉占位符文本。 */
function readKey(): string {
  const raw = import.meta.env.VITE_TMAP_KEY
  if (typeof raw !== 'string') return ''
  const key = raw.trim()
  if (!key) return ''
  // 占位符一律当作未配置，避免拿着"请替换"这种文本去请求
  if (key.toLowerCase().includes('please apply') || key.includes('请')) return ''
  return key
}

/** 是否已配置可用的密钥。组件可据此提前给出引导提示。 */
export const hasMapKey = (): boolean => readKey() !== ''

let pending: Promise<typeof TMap> | null = null

/**
 * 加载腾讯地图 SDK，返回全局 TMap。
 *
 * 同一页面多次调用只会插入一个 script 标签；重复调用复用同一个 Promise。
 */
export function loadTMap(): Promise<typeof TMap> {
  if (typeof TMap !== 'undefined') {
    return Promise.resolve(TMap)
  }

  const key = readKey()
  if (!key) {
    return Promise.reject(new Error(MAP_KEY_MISSING_MESSAGE))
  }

  if (pending) return pending

  pending = new Promise<typeof TMap>((resolve, reject) => {
    const script = document.createElement('script')
    script.type = 'text/javascript'
    script.async = true
    script.charset = 'utf-8'
    script.src = `${SDK_URL}&key=${encodeURIComponent(key)}`

    script.onload = () => {
      if (typeof TMap === 'undefined') {
        pending = null
        reject(new Error('腾讯地图 SDK 已加载但未暴露 TMap，请检查密钥是否有效。'))
        return
      }
      resolve(TMap)
    }

    script.onerror = () => {
      pending = null
      reject(new Error('腾讯地图 SDK 加载失败，请检查网络或密钥是否有效。'))
    }

    document.head.appendChild(script)
  })

  return pending
}
