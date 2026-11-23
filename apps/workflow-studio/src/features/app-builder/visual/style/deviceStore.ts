/**
 * The preview's device (phone / tablet / desktop) as shared state: the preview toolbar (AppPreviewPanel) writes it, the
 * style panel reads it to edit at that device's breakpoint (breakpoints.ts). `desktopOnly` is the style panel's own
 * choice at desktop: write `lg:` classes instead of the all-sizes base.
 */
import { create } from 'zustand'

export type Device = 'desktop' | 'tablet' | 'phone'

interface DeviceState {
  device: Device
  setDevice: (d: Device) => void
  desktopOnly: boolean
  setDesktopOnly: (on: boolean) => void
}

export const useDeviceStore = create<DeviceState>((set) => ({
  device: 'desktop',
  setDevice: (device) => set({ device }),
  desktopOnly: false,
  setDesktopOnly: (desktopOnly) => set({ desktopOnly }),
}))
