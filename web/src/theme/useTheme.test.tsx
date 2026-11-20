import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useTheme } from './useTheme'

describe('theme', () => {
  it('is light until the user picks one', () => {
    expect(renderHook(() => useTheme()).result.current.theme).toBe('light')
  })

  it('toggles and remembers the choice in this browser', () => {
    const { result } = renderHook(() => useTheme())
    act(() => result.current.toggle())
    expect(result.current.theme).toBe('dark')
    expect(renderHook(() => useTheme()).result.current.theme).toBe('dark')
  })

  it('works when storage is unavailable', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    expect(renderHook(() => useTheme()).result.current.theme).toBe('light')
    spy.mockRestore()
  })
})
