/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_BASE_URL: string
  readonly VITE_AI_SERVICE_BASE_URL?: string
  readonly VITE_AI_VIZ_ENABLED?: string
  readonly BASE_URL: string
  /** 腾讯地图密钥，写在 .env.local，不提交到版本库。见 utils/tmapLoader.ts */
  readonly VITE_TMAP_KEY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
