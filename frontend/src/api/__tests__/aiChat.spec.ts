import { afterEach, expect, it, vi } from 'vitest'
import { postAIChatStream } from '../aiChat'

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

it('keeps waiting past the old total timeout while rewrite progress arrives', async () => {
  vi.useFakeTimers()
  vi.stubGlobal('window', globalThis)
  let streamController!: ReadableStreamDefaultController<Uint8Array>
  let signal!: AbortSignal
  const stream = new ReadableStream<Uint8Array>({
    start(controller) { streamController = controller }
  })
  vi.stubGlobal('fetch', vi.fn(async (_url, options: RequestInit) => {
    signal = options.signal as AbortSignal
    return new Response(stream, { status: 200 })
  }))
  const onStatus = vi.fn()
  const pending = postAIChatStream('retry-session', '污染往哪传播？', null, onStatus)
  await vi.advanceTimersByTimeAsync(240000)
  const encoder = new TextEncoder()
  streamController.enqueue(encoder.encode('event: status\ndata: {"message":"正在完善回答，请稍候…"}\n\n'))
  await vi.advanceTimersByTimeAsync(0)
  await vi.advanceTimersByTimeAsync(240000)
  expect(signal.aborted).toBe(false)
  expect(onStatus).toHaveBeenCalledWith('正在完善回答，请稍候…')
  streamController.enqueue(encoder.encode('event: response\ndata: {"answer":"模拟传播向北延伸。","business_action":"reuse"}\n\n'))
  streamController.close()
  expect((await pending).answer).toBe('模拟传播向北延伸。')
  expect(vi.getTimerCount()).toBe(0)
})
