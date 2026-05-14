const query = new URLSearchParams(window.location.search)

export function getApiBase(): string {
  return (
    query.get("api") ||
    import.meta.env.VITE_API_URL ||
    (window.location.protocol === "file:" ? "http://127.0.0.1:8000/api" : "/api")
  ).replace(/\/$/, "")
}

export function getApiToken(): string {
  return query.get("token") || import.meta.env.VITE_API_TOKEN || ""
}

export function withAuthQuery(url: string): string {
  const token = getApiToken()
  if (!token) return url
  const next = new URL(url, window.location.href)
  next.searchParams.set("token", token)
  return next.toString()
}

export function withAuthHeaders(headers?: HeadersInit): Headers {
  const next = new Headers(headers)
  const token = getApiToken()
  if (token) {
    next.set("X-Framebench-Token", token)
    next.set("X-Film-Master-Token", token)
  }
  return next
}
