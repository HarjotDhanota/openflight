import { useEffect, useRef, useState, type RefObject } from 'react';
import type { Handed } from '../../stores/useSystemStore';
import { toScreen, type Px, type SetupViewRect } from './setupGeometry';
import { sameShape, type AimOverlay, type BoxOverlay } from './setupOverlay';
import './SetupPicture.css';

function useElementSize(ref: RefObject<HTMLElement | null>): { width: number; height: number } | null {
  const [size, setSize] = useState<{ width: number; height: number } | null>(null);
  useEffect(() => {
    const element = ref.current;
    if (!element) return undefined;
    const measure = () => {
      const { width, height } = element.getBoundingClientRect();
      setSize((previous) =>
        previous && previous.width === width && previous.height === height ? previous : { width, height }
      );
    };
    measure();
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure);
    observer?.observe(element);
    window.addEventListener('resize', measure);
    return () => {
      observer?.disconnect();
      window.removeEventListener('resize', measure);
    };
  }, [ref]);
  return size;
}

interface SetupPictureProps {
  src: string;
  alt: string;
  handed: Handed | null;
  box?: BoxOverlay | null;
  aim?: AimOverlay | null;
  className?: string;
}

/**
 * A camera picture, whole and centred, with the setup drawn on it through `toScreen`.
 * A left-hander's picture is mirrored as the Setup view's is. Nothing is drawn on a picture
 * of another shape than the one the setup was made on.
 */
export function SetupPicture({ src, alt, handed, box = null, aim = null, className = '' }: SetupPictureProps) {
  const ref = useRef<HTMLDivElement>(null);
  const size = useElementSize(ref);
  const [natural, setNatural] = useState<Px | null>(null);
  const imageSize = box?.imageSize ?? aim?.imageSize ?? null;
  const view: SetupViewRect | null =
    size && size.width > 0 && size.height > 0 && imageSize && natural && sameShape(imageSize, natural)
      ? { width: size.width, height: size.height, imageWidth: imageSize[0], imageHeight: imageSize[1] }
      : null;
  const screen = (px: Px) => toScreen(px, view!, handed);
  const scale = view ? Math.min(view.width / view.imageWidth, view.height / view.imageHeight) : 1;

  return (
    <div ref={ref} className={`setup-picture ${className}`.trim()}>
      <img
        src={src}
        alt={alt}
        className={`setup-picture__image${handed === 'LH' ? ' setup-picture__image--mirrored' : ''}`}
        onLoad={(event) => {
          const { naturalWidth, naturalHeight } = event.currentTarget;
          if (naturalWidth > 0 && naturalHeight > 0) {
            setNatural((previous) =>
              previous && previous[0] === naturalWidth && previous[1] === naturalHeight
                ? previous
                : [naturalWidth, naturalHeight]
            );
          }
        }}
      />
      {view && (box || aim) ? (
        <svg
          className="setup-picture__overlay"
          viewBox={`0 0 ${view.width} ${view.height}`}
          width={view.width}
          height={view.height}
          aria-hidden="true"
        >
          {box ? (
            <polygon className="setup-picture__box" points={box.outline.map((px) => screen(px).join(',')).join(' ')} />
          ) : null}
          {box?.ball ? (
            <circle
              className="setup-picture__ball"
              cx={screen(box.ball)[0]}
              cy={screen(box.ball)[1]}
              r={Math.max(((box.diameter ?? 0) / 2) * scale, 3)}
            />
          ) : null}
          {aim ? (
            <>
              <line
                className="setup-picture__aim-line"
                x1={screen(aim.from)[0]}
                y1={screen(aim.from)[1]}
                x2={screen(aim.to)[0]}
                y2={screen(aim.to)[1]}
              />
              <circle className="setup-picture__aim-point" cx={screen(aim.to)[0]} cy={screen(aim.to)[1]} r={3} />
            </>
          ) : null}
        </svg>
      ) : null}
    </div>
  );
}
