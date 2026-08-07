import { useCallback, useRef } from 'react';

/** Movement before a drag counts as a scroll rather than a tap, in px. */
const DRAG_THRESHOLD_PX = 6;

/**
 * Drag-to-scroll that works whatever the panel calls itself.
 *
 * The kiosk cannot be scrolled by CSS alone. Whether a touchscreen produces
 * touch events or mouse events depends on how X11 enumerates it, and many
 * 1024x600 panels present as a USB HID pointer -- so Chromium emits mouse
 * events, `touch-action` never applies, and a drag pans nothing. Relying on
 * native touch panning means relying on a property of the user's hardware.
 *
 * Pointer Events unify mouse, touch and pen, so this works either way and
 * needs no browser flags. Where native panning already works this simply
 * agrees with it.
 *
 * Taps are preserved: the pointer is only captured once movement passes a
 * threshold, so buttons and checkboxes still receive their click. Below that
 * threshold nothing is intercepted at all.
 */
export function useDragScroll<T extends HTMLElement>() {
  const ref = useRef<T | null>(null);
  const start = useRef<{ y: number; scrollTop: number; dragging: boolean } | null>(null);

  const onPointerDown = useCallback((event: React.PointerEvent<T>) => {
    // Let text entry keep its own caret placement and selection.
    if ((event.target as HTMLElement).closest('input, textarea')) return;
    const element = ref.current;
    if (!element) return;
    start.current = { y: event.clientY, scrollTop: element.scrollTop, dragging: false };
  }, []);

  const onPointerMove = useCallback((event: React.PointerEvent<T>) => {
    const element = ref.current;
    const from = start.current;
    if (!element || !from) return;

    const delta = from.y - event.clientY;
    if (!from.dragging) {
      if (Math.abs(delta) < DRAG_THRESHOLD_PX) return;
      from.dragging = true;
      // Capture only once this is definitely a scroll, so a tap that wanders
      // a pixel still lands on the button under it.
      element.setPointerCapture?.(event.pointerId);
    }
    element.scrollTop = from.scrollTop + delta;
  }, []);

  const end = useCallback((event: React.PointerEvent<T>) => {
    const element = ref.current;
    if (element?.hasPointerCapture?.(event.pointerId)) {
      element.releasePointerCapture(event.pointerId);
    }
    start.current = null;
  }, []);

  return {
    ref,
    /** Spread onto the scroll container. */
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp: end,
      onPointerCancel: end,
      onPointerLeave: end,
    },
  };
}
