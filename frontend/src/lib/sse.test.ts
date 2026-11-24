import { describe, expect, it } from 'vitest'
import { parseSSE } from './sse'

describe('parseSSE', () => {
  it('parses complete events and keeps the partial tail', () => {
    const input = 'event: token\ndata: {"text":"Hi"}\n\nevent: done\ndata: {}\n\nevent: tok'
    const { messages, rest } = parseSSE(input)
    expect(messages).toEqual([
      { event: 'token', data: '{"text":"Hi"}' },
      { event: 'done', data: '{}' },
    ])
    expect(rest).toBe('event: tok')
  })

  it('handles CRLF line endings', () => {
    const { messages } = parseSSE('event: token\r\ndata: x\r\n\r\n')
    expect(messages).toEqual([{ event: 'token', data: 'x' }])
  })
})
