import { t, type MessageKey } from '../../i18n';
import type { SetupSection, SetupSnapshot } from '../../hooks/useSetup';

export const SETUP_STEPS = ['box', 'aim', 'range', 'ready'] as const;
export type SetupStep = (typeof SETUP_STEPS)[number];

/** A section the kiosk can use: anything set, confirmed, skipped or defaulted. */
export function isUsable(section: SetupSection | undefined): boolean {
  return !!section && section.state !== 'not_set' && section.state !== 'stale';
}

function needsDoing(section: SetupSection | undefined): boolean {
  return !isUsable(section);
}

/** Whether the kiosk has a camera; an older snapshot that does not say is taken as yes. */
export function hasCamera(setup: SetupSnapshot): boolean {
  return setup.live.camera_available !== false;
}

/** Whether the kiosk as started can set up at all: a camera, and what else it needs (the tilt sensor). */
export function canSetUp(setup: SetupSnapshot): boolean {
  return hasCamera(setup) && setup.live.setup_possible !== false;
}

const STEP_SECTIONS = ['box', 'aim', 'ball_range', 'net_range'] as const;

/**
 * Whether to offer the Setup screen: a step is unset, stale or saved but not in effect, and the
 * unit has the camera and tilt sensor every step needs. The ready light holds red on the same
 * sections (setupNeedsCases.json keeps the two in step).
 */
export function needsSetup(setup: SetupSnapshot | null): boolean {
  if (!setup || !canSetUp(setup)) return false;
  return STEP_SECTIONS.some((name) => needsDoing(setup[name]) || name in setup.unapplied);
}

/** The index of the first step still to do; the ready check when none is. */
export function firstStepToDo(setup: SetupSnapshot | null): number {
  if (!setup || needsDoing(setup.box)) return 0;
  if (needsDoing(setup.aim) || needsDoing(setup.net_range)) return 1;
  if (needsDoing(setup.ball_range)) return 2;
  return 3;
}

const BALL_STATES = new Set(['found', 'outside_box', 'no_ball', 'ambiguous', 'waiting']);

/** The live look at the box, in words. */
export function ballWords(state: string | undefined): string {
  return t(`setup.ball.${state && BALL_STATES.has(state) ? state : 'waiting'}` as MessageKey);
}

const STATES = new Set(['not_set', 'confirmed', 'set', 'skipped', 'default', 'stale']);

export function stateWords(section: SetupSection): string {
  return STATES.has(section.state) ? t(`setup.state.${section.state}` as MessageKey) : section.state;
}

// beside a live "Ball found in the box", "Not set" reads as a contradiction
export function boxStateWords(section: SetupSection): string {
  return section.state === 'not_set' ? t('setup.state.notConfirmed') : stateWords(section);
}

// The service's reasons, by how they start; anything else is shown as it is.
const REASONS: Array<[RegExp, MessageKey]> = [
  [/^(no setup file|not set)$/, 'setup.reason.none'],
  [/^cleared$/, 'setup.reason.cleared'],
  [/^(unreadable|unknown schema)$/, 'setup.reason.unreadable'],
  [/^lens changed/, 'setup.reason.lens'],
  [/^rig changed/, 'setup.reason.rig'],
  [/^tilt (moved|changed)/, 'setup.reason.tilt'],
  [/^box changed/, 'setup.reason.box'],
  [/^box is /, 'setup.reason.needsBox'],
  [/^aim is /, 'setup.reason.needsAim'],
  [/^floor line changed/, 'setup.reason.floor'],
  [/^no inputs recorded$/, 'setup.reason.old'],
];

/** Why a section is unset or stale; nothing for a section never set, whose badge says so. */
export function reasonWords(reason: string | null | undefined): string {
  if (!reason) return '';
  const known = REASONS.find(([pattern]) => pattern.test(reason));
  return known ? t(known[1]) : reason;
}

/** Why an action was refused; null means the kiosk was not reached. */
export function refusalWords(reason: string | null): string {
  if (reason === null) return t('setup.unreachable');
  if (BALL_STATES.has(reason)) return ballWords(reason);
  if (/^box is /.test(reason)) return t('setup.needsBox');
  if (reason === 'camera unavailable' || reason === 'no camera frames') return t('setup.cameraNotReady');
  return reason;
}

function metres(value: number, digits = 2): string {
  return value.toFixed(digits);
}

// A labelled default keeps its own digits: the net default reads 4.6, not 4.60.
function plain(value: number): string {
  return String(Number(value.toFixed(2)));
}

export function rangeWords(section: SetupSection): string {
  if (section.state !== 'set' || section.range_m == null) return quietReason(section);
  const vars = { value: metres(section.range_m), spread: metres(section.uncertainty_m ?? 0) };
  return t(section.source === 'camera_prior' ? 'setup.range.fromCamera' : 'setup.range.fromRadar', vars);
}

export function netWords(section: SetupSection): string {
  if (section.value_m == null) return quietReason(section);
  if (section.ball_to_net_m != null) {
    return t(section.source === 'user_entered' ? 'setup.net.entered' : 'setup.net.ballDefault', {
      value: plain(section.ball_to_net_m / 0.3048),
    });
  }
  if (section.source === 'camera_floor_line' && section.uncertainty_m != null) {
    return t('setup.net.measured', { value: metres(section.value_m), spread: metres(section.uncertainty_m) });
  }
  const rejected = FLOOR_LINE_REJECTIONS[section.reason ?? ''];
  return rejected
    ? t('setup.net.defaultWithReason', { value: plain(section.value_m), reason: t(rejected) })
    : t('setup.net.default', { value: plain(section.value_m) });
}

// why the service kept the default for a floor line the user did mark
const FLOOR_LINE_REJECTIONS: Record<string, MessageKey> = {
  'above the horizon': 'setup.floor.aboveHorizon',
  'in front of the ball': 'setup.floor.inFront',
  'too close to the horizon to measure': 'setup.floor.nearHorizon',
  'too uncertain to measure': 'setup.floor.tooUncertain',
};

export function aimWords(section: SetupSection): string {
  if (section.state === 'skipped') return t('setup.aim.skipped');
  if (section.state !== 'set') return quietReason(section);
  return section.uncertainty_deg == null
    ? t('setup.aim.setNoSpread')
    : t('setup.aim.set', { spread: section.uncertainty_deg.toFixed(1) });
}

export function sceneWords(section: SetupSection): string {
  const height = section.lens_height_m;
  if (section.state === 'set' && height != null && section.source !== 'rig_nominal') {
    return t('setup.scene.solved', { value: metres(height, 3), spread: metres(section.uncertainty_m ?? 0, 3) });
  }
  // unset, stale or the rig's own: the kiosk runs on the enclosure's value
  return height != null && isUsable(section)
    ? t('setup.scene.rig', { value: metres(height, 3) })
    : t('setup.scene.rigNoValue');
}

/** The box beyond its badge: why it is unset or stale, if that says more than the badge. */
export function boxWords(section: SetupSection): string {
  return section.state === 'confirmed' ? '' : quietReason(section);
}

// "Not set yet" beside a NOT SET badge says nothing new
function quietReason(section: SetupSection): string {
  const words = reasonWords(section.reason);
  return words === t('setup.reason.none') ? '' : words;
}
