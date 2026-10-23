import { FreePlayActions, FreePlayControls } from '../components/FreePlayControls';
import type { UiExtension } from './index';

/** The tester's free play: its status beside the ready light and its live-view actions. */
const freePlay: UiExtension = {
  StatusControls: FreePlayControls,
  LiveActions: FreePlayActions,
};

export default freePlay;
