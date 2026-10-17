const DEVELOPMENT_API_BASE_URL = 'http://127.0.0.1:8000'

export function resolveApiBaseUrl(
  configuredUrl: string | undefined,
  isDevelopment: boolean,
): string {
  const baseUrl = configuredUrl ?? (
    isDevelopment ? DEVELOPMENT_API_BASE_URL : ''
  )
  return baseUrl.replace(/\/$/, '')
}

export const API_BASE_URL = resolveApiBaseUrl(
  import.meta.env.VITE_API_BASE_URL,
  import.meta.env.DEV,
)
