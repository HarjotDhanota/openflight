import { useDragScroll } from '../hooks/useDragScroll';
import { WeatherSettings } from './WeatherSettings';
import './SettingsView.css';

/**
 * Setup and settings screen.
 *
 * Shell for anything the user configures on the device rather than by CLI
 * flag. Weather is the first section; the leveling branch adds a bubble level
 * and tilt zeroing section alongside it, so keep sections independent and
 * self-contained.
 *
 * Drag-to-scroll is explicit rather than left to the browser: whether the
 * kiosk panel produces touch events or mouse events depends on how X11
 * enumerates it, and on a panel that reports as a pointer no amount of CSS
 * makes a drag pan. See useDragScroll.
 */
export function SettingsView() {
  const { ref, handlers } = useDragScroll<HTMLDivElement>();

  return (
    <div className="settings-view" ref={ref} {...handlers}>
      <WeatherSettings />
    </div>
  );
}
