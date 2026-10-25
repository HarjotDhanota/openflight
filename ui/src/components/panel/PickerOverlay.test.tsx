import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { PickerOverlay } from './PickerOverlay';
import { clubSections } from './pickerSections';

describe('PickerOverlay', () => {
  it('opens the Woods tab for the default driver so DR is the selected tile', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select club"
        selectedId="driver"
        sections={clubSections()}
        onSelect={() => {}}
        onClose={() => {}}
      />
    );

    expect(html).toContain('aria-label="Groups"');
    expect(html).toMatch(/panel-action--primary[^>]*>Woods</);
    expect(html).toMatch(/panel-action--secondary[^>]*>Irons</);
    expect(html).toMatch(/panel-action--secondary[^>]*>Hybrids</);
    expect(html).toMatch(/picker-overlay__option--selected[^>]*aria-pressed="true"[^>]*>DR</);
    expect(html).toContain('--picker-rows:3');
    expect(html).not.toContain('>7i<');
    expect(html).toContain('>3W<');
  });

  it('opens the Irons tab when a mid-iron is already selected', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select club"
        selectedId="7-iron"
        sections={clubSections()}
        onSelect={() => {}}
        onClose={() => {}}
      />
    );

    expect(html).toMatch(/panel-action--primary[^>]*>Irons</);
    expect(html).toMatch(/picker-overlay__option--selected[^>]*aria-pressed="true"[^>]*>7i</);
    expect(html).not.toContain('>DR<');
  });

  it('offers the hand as a one-tap Right / Left choice beside the clubs', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select club"
        selectedId="driver"
        sections={clubSections()}
        onSelect={() => {}}
        onClose={() => {}}
        hand={{ handed: 'RH', source: 'default', onChange: () => {} }}
      />
    );

    expect(html).toContain('aria-label="Hand"');
    expect(html).toMatch(/aria-pressed="true"[^>]*>Right-handed</);
    expect(html).toMatch(/aria-pressed="false"[^>]*>Left-handed</);
    expect(html).not.toContain('disabled');
  });

  it('shows the left hand selected once the kiosk says so', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select club"
        selectedId="driver"
        sections={clubSections()}
        onSelect={() => {}}
        onClose={() => {}}
        hand={{ handed: 'LH', source: 'profile', onChange: () => {} }}
      />
    );

    expect(html).toMatch(/aria-pressed="true"[^>]*>Left-handed</);
    expect(html).toMatch(/aria-pressed="false"[^>]*>Right-handed</);
  });

  it('says when a connected simulator sets the hand, and locks the choice', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select club"
        selectedId="driver"
        sections={clubSections()}
        onSelect={() => {}}
        onClose={() => {}}
        hand={{ handed: 'LH', source: 'simulator', onChange: () => {} }}
      />
    );

    expect(html).toContain('Set by the simulator');
    expect(html.match(/<button[^>]*disabled=""[^>]*>(Right|Left)-handed</g)).toHaveLength(2);
  });

  it('has no hand choice for training implements', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select implement"
        selectedId="driver"
        sections={clubSections()}
        onSelect={() => {}}
        onClose={() => {}}
      />
    );

    expect(html).not.toContain('aria-label="Hand"');
  });
});
