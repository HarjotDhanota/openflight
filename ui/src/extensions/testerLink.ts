import { TesterButton } from '../components/TesterButton';
import { openedFromTester } from '../utils/testerPage';
import type { UiExtension } from './index';

/**
 * The way back to the tester page (25 Oct): a Tester button beside the ready light on
 * every tab, while a tester page answers for this unit. A kiosk the tester page showed
 * in its own window already has its club (the page chose it): no club picker on load.
 */
const testerLink: UiExtension = {
  StatusControls: TesterButton,
  clubPickerOnLoad: () => typeof window === 'undefined' || !openedFromTester(window),
};

export default testerLink;
