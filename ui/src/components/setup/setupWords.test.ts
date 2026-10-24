import { afterEach, describe, expect, it } from 'vitest';
import { setActiveLocale } from '../../i18n';
import type { SetupSnapshot } from '../../hooks/useSetup';
import {
  aimWords,
  boxStateWords,
  firstStepToDo,
  needsSetup,
  netWords,
  rangeWords,
  reasonWords,
  refusalWords,
  sceneWords,
} from './setupWords';
import needsCases from './setupNeedsCases.json';

const notSet = { state: 'not_set', reason: 'no setup file' };

function snapshot(overrides: Partial<SetupSnapshot> = {}): SetupSnapshot {
  return {
    schema: 'openflight.setup.v1',
    revision: 1,
    box: notSet,
    aim: notSet,
    ball_range: notSet,
    net_range: notSet,
    scene: notSet,
    live: {},
    unapplied: {},
    ...overrides,
  };
}

const done = snapshot({
  box: { state: 'confirmed', ball_px: [640, 600], box_px: [500, 500, 780, 700] },
  aim: { state: 'set', aim_px: [650, 300], uncertainty_deg: 0.84 },
  ball_range: { state: 'set', range_m: 2.312, uncertainty_m: 0.118, source: 'camera_prior' },
  net_range: { state: 'default', value_m: 4.6, source: 'default_not_measured', reason: 'no floor line marked' },
});

describe('setup words', () => {
  afterEach(() => setActiveLocale('en'));

  it('says the range, its ± and where it came from', () => {
    expect(rangeWords(done.ball_range)).toBe('2.31 m ± 0.12, from the camera; the radar refines it each shot');
    expect(rangeWords({ state: 'set', range_m: 2.4, uncertainty_m: 0.05, source: 'radar' })).toBe(
      '2.40 m ± 0.05, from the radar'
    );
  });

  it('shows an unmeasured net as the default, labelled', () => {
    expect(netWords(done.net_range)).toBe('4.6 m (not measured)');
    expect(netWords({ state: 'set', value_m: 3.917, uncertainty_m: 0.204, source: 'camera_floor_line' })).toBe(
      '3.92 m ± 0.20, from the floor line'
    );
  });

  it('says why a floor line gave no net distance', () => {
    const rejected = (reason: string) => ({
      state: 'default',
      value_m: 4.6,
      source: 'default_not_measured',
      reason,
    });
    expect(netWords(rejected('above the horizon'))).toBe('4.6 m (not measured): the floor line is above the horizon');
    expect(netWords(rejected('in front of the ball'))).toBe(
      '4.6 m (not measured): the floor line is nearer than the ball'
    );
    expect(netWords(rejected('too close to the horizon to measure'))).toBe(
      '4.6 m (not measured): the floor line is too close to the horizon to measure'
    );
    expect(netWords(rejected('too uncertain to measure'))).toBe(
      '4.6 m (not measured): the floor line is too uncertain to measure'
    );
    expect(netWords(rejected('aim point skipped'))).toBe('4.6 m (not measured)');
  });

  it('says a box not yet saved is not confirmed', () => {
    expect(boxStateWords({ state: 'not_set', reason: 'no setup file' })).toBe('Not confirmed');
    expect(boxStateWords({ state: 'confirmed' })).toBe('Confirmed');
    expect(boxStateWords({ state: 'stale', reason: 'lens changed (a -> b)' })).toBe('Needs redoing');
  });

  it('says the aim and a skipped aim', () => {
    expect(aimWords(done.aim)).toBe('Set, ± 0.8°');
    expect(aimWords({ state: 'skipped', reason: 'the tester skipped the aim point' })).toBe(
      "Skipped: left and right are from the unit's aim"
    );
  });

  it('says the lens height and whether it was measured', () => {
    expect(sceneWords({ state: 'default', lens_height_m: 0.152, source: 'rig_nominal' })).toBe(
      "0.152 m, the enclosure's value (not measured)"
    );
    expect(sceneWords(notSet)).toBe("The enclosure's value (not measured)");
    expect(sceneWords({ state: 'set', lens_height_m: 0.161, uncertainty_m: 0.004, source: 'radar_solved' })).toBe(
      '0.161 m ± 0.004, solved by the radar'
    );
  });

  it('turns the stored reasons into plain words', () => {
    expect(reasonWords('no setup file')).toBe('Not set yet');
    expect(reasonWords('cleared')).toBe('Cleared');
    expect(reasonWords('lens changed (stock -> 6mm)')).toBe('The lens changed');
    expect(reasonWords('rig changed (abc -> def)')).toBe('The enclosure changed');
    expect(reasonWords('tilt moved 2.3 deg, more than the 1 deg margin')).toBe('The camera tilt changed');
    expect(reasonWords('box changed (a -> b)')).toBe('The ball box changed');
    expect(reasonWords('box is stale: lens changed (a -> b)')).toBe('The ball box needs redoing');
    expect(reasonWords('aim is not_set: cleared')).toBe('The aim needs redoing');
    expect(reasonWords('something new')).toBe('something new');
  });

  it('turns a refusal into plain words', () => {
    expect(refusalWords('no_ball')).toBe('No ball seen. Put a ball in the box.');
    expect(refusalWords('outside_box')).toBe('The ball is outside the box. Move it in.');
    expect(refusalWords(null)).toBe("Couldn't reach the kiosk. Try again.");
    expect(refusalWords('camera unavailable')).toBe('The camera has no picture yet. Try again.');
    expect(refusalWords('no camera frames')).toBe('The camera has no picture yet. Try again.');
    expect(refusalWords('something else')).toBe('something else');
  });

  it('offers setup while a step the user can do is unset or stale', () => {
    expect(needsSetup(null)).toBe(false);
    expect(needsSetup(snapshot())).toBe(true);
    expect(needsSetup(done)).toBe(false);
    expect(needsSetup({ ...done, aim: { state: 'skipped' } })).toBe(false);
    expect(needsSetup({ ...done, ball_range: { state: 'stale', reason: 'box changed (a -> b)' } })).toBe(true);
    // the lens height has no step on this screen: the kiosk runs on the enclosure's value
    expect(needsSetup({ ...done, scene: { state: 'stale', reason: 'box changed' } })).toBe(false);
    // a unit without a camera can never set up: never offer it
    expect(needsSetup(snapshot({ live: { camera_available: false } }))).toBe(false);
    expect(needsSetup(snapshot({ live: { camera_available: true } }))).toBe(true);
    // nor one that lacks what setup needs (the tilt sensor): it would nag forever
    expect(needsSetup(snapshot({ live: { camera_available: true, setup_possible: false } }))).toBe(false);
  });

  // the same table runs the kiosk's setup_incomplete cause in tests/test_ready_light.py
  for (const item of needsCases.cases) {
    it(`agrees with the ready light: ${item.name}`, () => {
      const sections = { ...needsCases.base, ...item.sections } as Record<string, SetupSnapshot['box']>;
      const setup = {
        revision: 1,
        box: sections.box,
        aim: sections.aim,
        ball_range: sections.ball_range,
        net_range: sections.net_range,
        scene: sections.scene,
        live: { camera_available: item.camera, setup_possible: 'possible' in item ? item.possible : true },
        unapplied: item.unapplied as Record<string, string>,
      };
      expect(needsSetup(setup)).toBe(item.needs);
    });
  }

  it('opens on the first step left to do', () => {
    expect(firstStepToDo(null)).toBe(0);
    expect(firstStepToDo(snapshot())).toBe(0);
    expect(firstStepToDo({ ...done, aim: notSet })).toBe(1);
    expect(firstStepToDo({ ...done, net_range: { state: 'stale', reason: 'tilt changed' } })).toBe(1);
    expect(firstStepToDo({ ...done, ball_range: notSet })).toBe(2);
    expect(firstStepToDo(done)).toBe(3);
  });

  it('speaks the active language', () => {
    setActiveLocale('es');
    expect(netWords(done.net_range)).toContain('4.6 m');
    expect(netWords(done.net_range)).not.toBe('4.6 m (not measured)');
  });
});
