// Server-sent events from a fetch() body (POST /api/ask streams its reply, which EventSource cannot do).
import type { RunEvent } from './types'

export class SseParser {
  private buffer = ''

  /** Feed a chunk of text; returns the events completed by it. */
  push(chunk: string): RunEvent[] {
    this.buffer += chunk.replace(/\r\n/g, '\n')
    const events: RunEvent[] = []
    let end: number
    while ((end = this.buffer.indexOf('\n\n')) >= 0) {
      const block = this.buffer.slice(0, end)
      this.buffer = this.buffer.slice(end + 2)
      const data = block.split('\n').filter(l => l.startsWith('data:')).map(l => l.slice(5).trimStart()).join('\n')
      if (data) events.push(JSON.parse(data) as RunEvent)
    }
    return events
  }
}
