import { describe, expect, it } from 'vitest'
import { SseParser } from './sse'

describe('server-sent events', () => {
  it('parses events split across chunks', () => {
    const p = new SseParser()
    expect(p.push('data: {"type":"started","run":{"run_id"')).toEqual([])
    expect(p.push(':"r1"}}\n\ndata: {"type":"done","status":"complete"}\n')).toEqual([{ type: 'started', run: { run_id: 'r1' } }])
    expect(p.push('\n')).toEqual([{ type: 'done', status: 'complete' }])
  })

  it('ignores comments and keep-alive lines', () => {
    const p = new SseParser()
    expect(p.push(': ping\n\ndata: {"type":"message","text":"hi"}\n\n')).toEqual([{ type: 'message', text: 'hi' }])
  })

  it('handles CRLF line endings', () => {
    expect(new SseParser().push('data: {"type":"done","status":"failed"}\r\n\r\n')).toEqual([{ type: 'done', status: 'failed' }])
  })
})
