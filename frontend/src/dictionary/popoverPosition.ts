import type { CSSProperties } from 'react'


const POPOVER_WIDTH = 340
const VIEWPORT_EDGE = 12
const ANCHOR_GAP = 8
const NARROW_VIEWPORT = 640
const BOTTOM_SHEET_MAX_WIDTH = 360

export interface DictionaryPopoverPosition {
  placement: 'above' | 'below' | 'sheet'
  style: CSSProperties
}

export function calculateDictionaryPopoverPosition(
  anchor: Pick<DOMRect, 'top' | 'right' | 'bottom' | 'left'>,
  popoverHeight: number,
  viewportWidth: number,
  viewportHeight: number,
): DictionaryPopoverPosition {
  const maxHeight = Math.max(180, viewportHeight - VIEWPORT_EDGE * 2)
  if (viewportWidth <= NARROW_VIEWPORT) {
    const horizontalInset = Math.max(
      VIEWPORT_EDGE,
      (viewportWidth - BOTTOM_SHEET_MAX_WIDTH) / 2,
    )
    return {
      placement: 'sheet',
      style: {
        position: 'fixed',
        right: horizontalInset,
        bottom: VIEWPORT_EDGE,
        left: horizontalInset,
        maxHeight,
      },
    }
  }

  const width = Math.min(POPOVER_WIDTH, viewportWidth - VIEWPORT_EDGE * 2)
  const effectiveHeight = Math.min(popoverHeight || 420, maxHeight)
  const roomBelow = viewportHeight - anchor.bottom - ANCHOR_GAP - VIEWPORT_EDGE
  const roomAbove = anchor.top - ANCHOR_GAP - VIEWPORT_EDGE
  const placement = roomBelow >= Math.min(effectiveHeight, 320) || roomBelow >= roomAbove
    ? 'below'
    : 'above'
  const left = Math.min(
    Math.max(VIEWPORT_EDGE, anchor.left),
    viewportWidth - width - VIEWPORT_EDGE,
  )
  const top = placement === 'below'
    ? Math.min(anchor.bottom + ANCHOR_GAP, viewportHeight - effectiveHeight - VIEWPORT_EDGE)
    : Math.max(VIEWPORT_EDGE, anchor.top - ANCHOR_GAP - effectiveHeight)
  return {
    placement,
    style: { position: 'fixed', top, left, width, maxHeight },
  }
}
