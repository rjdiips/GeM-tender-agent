import { act, renderHook } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { useTheme } from './useTheme'

function system(dark: boolean) {
  window.matchMedia = vi.fn().mockReturnValue({ matches: dark, addEventListener: vi.fn(), removeEventListener: vi.fn() })
}

describe('theme', () => {
  it('follows the operating system until the user picks one', () => {
    system(false)
    expect(renderHook(() => useTheme()).result.current.theme).toBe('light')
    system(true)
    expect(renderHook(() => useTheme()).result.current.theme).toBe('dark')
  })

  it('toggles and remembers the choice in this browser', () => {
    system(true)
    const { result } = renderHook(() => useTheme())
    act(() => result.current.toggle())
    expect(result.current.theme).toBe('light')
    expect(renderHook(() => useTheme()).result.current.theme).toBe('light')
  })

  it('works when storage is unavailable', () => {
    system(true)
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked') })
    expect(renderHook(() => useTheme()).result.current.theme).toBe('dark')
    spy.mockRestore()
  })
})
