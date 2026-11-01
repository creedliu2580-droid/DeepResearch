const BASE = "/api"

export async function fetchModels(): Promise<{
  models: { role: string; model: string }[]
  available: string[]
}> {
  const res = await fetch(`${BASE}/models`)
  if (!res.ok) return { models: [], available: [] }
  const data = await res.json()
  return { models: data.models ?? [], available: data.available ?? [] }
}

/**
 * 发起 SSE POST 流 —— EventSource 只支持 GET，这里用 fetch + ReadableStream 手动解析 SSE。
 */
export async function streamPost(
  path: string,
  body: unknown,
  callbacks: {
    onEvent: (evt: Record<string, unknown>) => void
    onError?: (msg: string) => void
  },
  signal?: AbortSignal,
): Promise<void> {
  try {
    const res = await fetch(`${BASE}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Accept": "text/event-stream" },
      body: JSON.stringify(body),
      signal,
    })

    if (!res.ok || !res.body) {
      callbacks.onError?.(`stream failed: ${res.status}`)
      return
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ""

    while (true) {
      const { value, done } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // SSE 按 \n\n 分块
      let idx: number
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const chunk = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)

        const dataLine = chunk.split("\n").find((l) => l.startsWith("data:"))
        if (!dataLine) continue
        const json = dataLine.slice(5).trim()
        try {
          const evt = JSON.parse(json)
          callbacks.onEvent(evt)
          if (evt.type === "done" || evt.type === "error") return
        } catch {
          // 忽略坏数据
        }
      }
    }
  } catch (err) {
    // 用户主动取消（AbortController.abort）不当作错误
    if ((err as { name?: string }).name === "AbortError") return
    callbacks.onError?.(err instanceof Error ? err.message : String(err))
  }
}
