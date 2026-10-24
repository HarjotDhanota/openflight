import { useEffect, useState } from 'react';

export type CameraStillState = 'checking' | 'available' | 'unavailable';

/**
 * A camera still refreshed every `refreshMs`: the latest frame as an object URL. A 404
 * means the kiosk has no camera capture, and polling stops; any other failure keeps the
 * last frame and retries.
 */
export function useCameraStill(
  url: string,
  refreshMs: number
): { state: CameraStillState; src: string | null; updatedAt: Date | null } {
  const [state, setState] = useState<CameraStillState>('checking');
  const [src, setSrc] = useState<string | null>(null);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    let timer: ReturnType<typeof setInterval> | null = null;

    const refresh = async () => {
      try {
        const response = await fetch(`${url}?t=${Date.now()}`, { cache: 'no-store' });
        if (cancelled) return;
        if (response.status === 404) {
          setState('unavailable');
          if (timer) clearInterval(timer);
          return;
        }
        if (!response.ok) return;
        const blob = await response.blob();
        if (cancelled) return;
        const nextUrl = URL.createObjectURL(blob);
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = nextUrl;
        setSrc(nextUrl);
        setUpdatedAt(new Date());
        setState('available');
      } catch {
        // Keep the last frame and retry after a transient network failure.
      }
    };

    refresh();
    timer = setInterval(refresh, refreshMs);
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url, refreshMs]);

  return { state, src, updatedAt };
}
