import { useEffect, useRef, useState, type ReactNode } from 'react';
import Logo from '../../logo/Logo';
import { TabBar } from '../ui/TabBar';
import type { PanelView } from './views';
import { PANEL_VIEWS } from './views';
import { useI18n } from '../../i18n/useI18n';
import type { MessageKey } from '../../i18n';
import { useUnitPreference } from '../../state/useUnitPreference';
import { getUnitsLabel } from '../../utils/units';
import { useSystemStore } from '../../stores/useSystemStore';
import { PowerExperience } from '../PowerStatus';
import type { PowerStatus } from '../../types/power';
import { scrollHint } from '../../utils/scrollHint';

interface PanelFooterProps {
  currentView: PanelView;
  onChangeView: (view: PanelView) => void;
  onOpenMenu: () => void;
  onShutdown: () => void;
  menuOpen: boolean;
  shotCount: number;
  debugRecording: boolean;
  /** Setup has a step to do: the Setup tab carries a dot. */
  setupOffered?: boolean;
  /** Replaces the logo when Launch Daddy mode is active. */
  brand?: ReactNode;
  /**
   * Battery telemetry. Omit to read `useSystemStore`. Pass it in tests because
   * `renderToString` keeps the store's server snapshot at `null`.
   */
  powerStatus?: PowerStatus | null;
}

const VIEWS_WITH_UNITS: ReadonlySet<PanelView> = new Set(['live', 'club', 'flight', 'stats', 'shots']);

/**
 * Bottom bar: menu button, divider-separated panel tabs, and view meta on the
 * right. Panel actions live in `PanelHeader`.
 *
 * The mockup footer shows four tabs; Profiles and Debug are extra working
 * screens. Burying either behind a gesture makes them unreachable on the kiosk.
 */
export function PanelFooter({
  currentView,
  onChangeView,
  onOpenMenu,
  onShutdown,
  menuOpen,
  shotCount,
  debugRecording,
  setupOffered = false,
  brand,
  powerStatus: powerStatusProp,
}: PanelFooterProps) {
  const { t } = useI18n();
  const { unitSystem } = useUnitPreference();
  const unitsLabel = getUnitsLabel(unitSystem);
  const showUnits = VIEWS_WITH_UNITS.has(currentView);
  const storePowerStatus = useSystemStore((state) => state.powerStatus);
  const powerStatus = powerStatusProp !== undefined ? powerStatusProp : storePowerStatus;
  // the tabs scroll sideways on narrow screens: a › says more are there, and taps along
  const tabsWrapRef = useRef<HTMLDivElement>(null);
  const [moreTabs, setMoreTabs] = useState({ left: false, right: false });
  useEffect(() => {
    const tabs = tabsWrapRef.current?.querySelector<HTMLElement>('.panel-footer__tabs');
    if (!tabs) return undefined;
    const update = () => {
      const next = scrollHint(tabs);
      setMoreTabs((prev) => (prev.left === next.left && prev.right === next.right ? prev : next));
    };
    update();
    tabs.addEventListener('scroll', update, { passive: true });
    window.addEventListener('resize', update);
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(update);
    observer?.observe(tabs);
    return () => {
      tabs.removeEventListener('scroll', update);
      window.removeEventListener('resize', update);
      observer?.disconnect();
    };
  }, []);
  const scrollTabs = () => {
    const tabs = tabsWrapRef.current?.querySelector<HTMLElement>('.panel-footer__tabs');
    tabs?.scrollBy({ left: Math.max(tabs.clientWidth * 0.6, 80), behavior: 'smooth' });
  };
  const options = PANEL_VIEWS.map((view) => {
    const label = t(`nav.${view.id}` as MessageKey);
    switch (view.id) {
      case 'shots':
        return {
          ...view,
          label,
          badge: shotCount > 0 ? <span className="nav__badge">{shotCount}</span> : undefined,
        };
      case 'setup':
        return { ...view, label, badge: setupOffered ? <span className="nav__setup-dot" /> : undefined };
      case 'debug':
        return {
          ...view,
          label,
          extraClassName: debugRecording ? 'nav__button--recording' : undefined,
          badge: debugRecording ? <span className="nav__recording-dot" /> : undefined,
        };
      default:
        return { ...view, label };
    }
  });

  return (
    <div className="panel-footer">
      <button
        type="button"
        className="panel-footer__menu"
        style={{ border: 'none' }}
        onClick={onOpenMenu}
        aria-expanded={menuOpen}
        aria-label={t('nav.openMenu')}
      >
        {brand ?? <Logo size="small" variant="mono" />}
      </button>

      <div className="panel-footer__nav">
        <div
          className="panel-footer__tabs-wrap"
          ref={tabsWrapRef}
          data-more-left={String(moreTabs.left)}
          data-more-right={String(moreTabs.right)}
        >
          <TabBar
            className="panel-footer__tabs"
            ariaLabel={t('nav.panels')}
            value={currentView}
            onChange={onChangeView}
            options={options}
            separator={<span className="panel-header__divider" aria-hidden="true" />}
          />
          {moreTabs.right ? (
            <button type="button" className="panel-footer__more" aria-label={t('nav.moreTabs')} onClick={scrollTabs}>
              <span aria-hidden="true">›</span>
            </button>
          ) : null}
        </div>
      </div>

      <div className="panel-footer__meta">
        {showUnits ? <span className="panel-footer__units">{unitsLabel}</span> : null}
        {powerStatus ? <PowerExperience status={powerStatus} variant="chrome" /> : null}
        <button
          type="button"
          className="panel-footer__power"
          onClick={onShutdown}
          aria-label={t('menu.shutdown')}
          title={t('menu.shutdown')}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
            <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
            <line x1="12" y1="2" x2="12" y2="12" />
          </svg>
        </button>
      </div>
    </div>
  );
}
