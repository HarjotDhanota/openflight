import { WeatherSettings } from './WeatherSettings';
import './SettingsView.css';

/**
 * Setup and settings screen.
 *
 * Shell for anything the user configures on the device rather than by CLI
 * flag. Weather is the first section; the leveling branch adds a bubble level
 * and tilt zeroing section alongside it, so keep sections independent and
 * self-contained.
 */
export function SettingsView() {
  return (
    <div className="settings-view">
      <WeatherSettings />
    </div>
  );
}
