import { useEffect, useMemo, useState } from 'react';
import { loadInitial, regenerate } from './api';
import { CriteriaTable } from './CriteriaTable';
import { label, mm, overlayPath, percent, rejectionTotal } from './model';
import type { Controls, Frame, Session, Shot, StudioOptions } from './types';
import './app.css';

const overlayNames = ['silhouette', 'truth_mask', 'template', 'track', 'extrapolation'] as const;
type OverlayName = (typeof overlayNames)[number];

function Metric({ name, value, detail, tone = '' }: { name: string; value: string; detail: string; tone?: string }) {
  return <div className={`metric ${tone}`}><span>{name}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function ControlsPanel({ controls, options, busy, onChange, onRun }: {
  controls: Controls; options: StudioOptions; busy: boolean;
  onChange: (next: Controls) => void; onRun: () => void;
}) {
  const update = <K extends keyof Controls>(key: K, value: Controls[K]) => onChange({ ...controls, [key]: value });
  return (
    <aside className="control-panel">
      <div className="panel-heading"><span>LIVE LAB</span><h2>Scenario controls</h2><p>Regenerate artifacts + run the production classical solver.</p></div>
      <label>Club<select value={controls.club} onChange={(event) => update('club', event.target.value)}>{options.clubs.map((item) => <option key={item} value={item}>{label(item)}</option>)}</select></label>
      <label>Shots (N)<select value={controls.n} onChange={(event) => update('n', Number(event.target.value))}>{options.n.map((item) => <option key={item}>{item}</option>)}</select></label>
      <label>Exposure candidate<select value={controls.candidate} onChange={(event) => update('candidate', event.target.value)}>{options.candidates.map((item) => <option key={item.id} value={item.id}>{item.label}{item.role === 'comparison_only' ? ' · comparison' : ''}</option>)}</select></label>
      <label>Template variation<select value={String(controls.template_variation)} onChange={(event) => update('template_variation', event.target.value === 'calibrated' || event.target.value === 'population' ? event.target.value : Number(event.target.value))}>{options.template_variations.map((item) => <option key={String(item)} value={String(item)}>{item === 'calibrated' ? 'Calibrated (±1%)' : item === 'population' ? 'Population (club-specific)' : `±${Number(item) * 100}%`}</option>)}</select></label>
      <label>Radar residual<select value={controls.radar_residual_mm} onChange={(event) => update('radar_residual_mm', Number(event.target.value))}>{options.radar_residual_mm.map((item) => <option key={item} value={item}>{item > 0 ? '+' : ''}{item} mm</option>)}</select></label>
      <label>Sync mode<select value={controls.sync_mode} onChange={(event) => update('sync_mode', event.target.value)}>{options.sync_modes.map((item) => <option key={item} value={item}>{label(item)}</option>)}</select></label>
      {controls.club === 'poc_driver' && <label>Club speed<select value={controls.club_speed_mph ?? ''} onChange={(event) => update('club_speed_mph', event.target.value ? Number(event.target.value) : null)}><option value="">Population draw</option>{options.driver_speed_mph.map((item) => <option key={item} value={item}>{item} mph</option>)}</select></label>}
      <button className="run-button" type="button" disabled={busy} onClick={onRun}>{busy ? <><i /> Solving…</> : 'Regenerate + solve'}</button>
      <p className="control-note">Local deterministic research run. N is capped at 32 for interactive use.</p>
    </aside>
  );
}

function SpeedStrip({ rows }: { rows: Session['landing']['club_speed_sweep'] }) {
  const ambient = rows.filter((row) => row.candidate === 'ambient_500us');
  if (!ambient.length) return null;
  return (
    <section className="speed-strip">
      <div><span className="eyebrow">OFFICIAL DEGRADATION AXIS</span><h3>Ambient across driver speed</h3></div>
      <div className="speed-points">{ambient.map((row) => <div key={String(row.value)}><strong>{row.value} <small>mph</small></strong><span>{percent(Number(row.solve_rate))} solve</span><span>{Number(row.impact_error_mm_median).toFixed(2)} / {Number(row.impact_error_mm_p90).toFixed(2)} mm med/p90</span></div>)}</div>
    </section>
  );
}

function FrameCanvas({ frame, enabled }: { frame: Frame; enabled: Set<OverlayName> }) {
  const o = frame.overlays;
  return (
    <div className="frame-canvas">
      <img src={frame.image} alt={`Camera frame ${frame.index}`} />
      <svg viewBox="0 0 320 200" aria-label="Diagnostic overlays">
        {enabled.has('truth_mask') && <path className="truth-mask" d={overlayPath(o.truth_mask, true)} />}
        {enabled.has('silhouette') && <path className="silhouette" d={overlayPath(o.silhouette, true)} />}
        {enabled.has('template') && <path className="template" d={overlayPath(o.template, true)} />}
        {enabled.has('track') && <path className="track" d={overlayPath(o.track, false)} />}
        {enabled.has('extrapolation') && <path className="extrapolation" d={overlayPath(o.extrapolation, false)} />}
        <path className="radar-ray" d={overlayPath(o.radar_ray, false)} />
        <circle className="face-center" cx={o.face_center[0]} cy={o.face_center[1]} r="2.5" />
      </svg>
      <span className="frame-stamp">F{String(frame.index).padStart(2, '0')} · {frame.time_ms.toFixed(2)} ms</span>
    </div>
  );
}

function FrameInspector({ shot }: { shot: Shot }) {
  const [frameIndex, setFrameIndex] = useState(0);
  const [enabled, setEnabled] = useState<Set<OverlayName>>(new Set(overlayNames));
  const frame = shot.frames[Math.min(frameIndex, shot.frames.length - 1)];
  const toggle = (name: OverlayName) => setEnabled((current) => {
    const next = new Set(current); if (next.has(name)) next.delete(name); else next.add(name); return next;
  });
  if (!frame) return null;
  return (
    <section className="inspector-card">
      <div className="section-title"><div><span className="eyebrow">CAMERA EVIDENCE</span><h2>Frame stepper</h2></div><span className="seed">seed {shot.seed}</span></div>
      <FrameCanvas frame={frame} enabled={enabled} />
      <div className="overlay-toggles">{overlayNames.map((name) => <button key={name} className={enabled.has(name) ? `active ${name}` : ''} onClick={() => toggle(name)} type="button"><i />{label(name)}</button>)}</div>
      <div className="frame-stepper">{shot.frames.map((item) => <button type="button" key={item.index} className={`${item.index === frame.index ? 'active' : ''} ${shot.timeline.used_frame_indices.includes(item.index) ? 'used' : ''}`} onClick={() => setFrameIndex(item.index)}><span>F{item.index}</span><small>{item.time_ms.toFixed(1)}</small></button>)}</div>
      <div className="exposure-readout"><span>Exposure window</span><strong>{frame.exposure_start_ms.toFixed(3)} → {frame.exposure_end_ms.toFixed(3)} ms</strong><span>{shot.timeline.used_frame_indices.includes(frame.index) ? 'USED IN SOLVE' : 'NOT USED'}</span></div>
    </section>
  );
}

function Clubface({ shot }: { shot: Shot }) {
  const { u, v } = shot.clubface.limits_mm;
  const point = (impact: [number, number]) => [140 + impact[0] / u * 105, 105 - impact[1] / v * 72];
  const truth = point(shot.clubface.impact.truth);
  const estimate = shot.clubface.impact.estimated ? point(shot.clubface.impact.estimated) : null;
  return (
    <section className="impact-card">
      <div className="section-title"><div><span className="eyebrow">IMPACT RECONSTRUCTION</span><h2>Clubface result</h2></div><span className={`solve-pill ${shot.ok ? 'ok' : 'rejected'}`}>{shot.ok ? 'SOLVED' : 'REJECTED'}</span></div>
      <svg className="clubface" viewBox="0 0 280 210" role="img" aria-label="Estimated and truth impact on clubface">
        <defs><linearGradient id="face" x1="0" x2="1"><stop stopColor="#1a2732"/><stop offset="1" stopColor="#0f171e"/></linearGradient></defs>
        <path d="M35 58 Q45 28 85 23 L217 38 Q247 43 252 76 L244 149 Q239 178 203 185 L72 179 Q34 175 28 143 Z" fill="url(#face)" stroke="#627483" strokeWidth="2"/>
        {[62,82,102,122,142,162].map((y) => <path key={y} d={`M40 ${y} Q140 ${y - 8} 240 ${y + 3}`} className="groove"/>)}
        <path d="M140 31 V181 M32 105 H248" className="face-axis"/>
        <line x1={truth[0]} y1={truth[1]} x2={estimate?.[0] ?? truth[0]} y2={estimate?.[1] ?? truth[1]} className="error-vector" />
        <circle cx={truth[0]} cy={truth[1]} r="7" className="truth-dot"/><path d={`M${truth[0]-4} ${truth[1]}h8M${truth[0]} ${truth[1]-4}v8`} className="truth-cross"/>
        {estimate && <circle cx={estimate[0]} cy={estimate[1]} r="5.5" className="estimate-dot"/>}
      </svg>
      <div className="impact-legend"><span><i className="estimate-dot"/>Estimate {shot.clubface.impact.estimated ? `${shot.clubface.impact.estimated.map((x) => x.toFixed(1)).join(', ')} mm` : '—'}</span><span><i className="truth-dot"/>Truth {shot.clubface.impact.truth.map((x) => x.toFixed(1)).join(', ')} mm</span></div>
      <div className="impact-error"><span>Vector error</span><strong>{mm(shot.impact_error_mm)}</strong><small>{shot.diagnostics.rejection_reason ?? 'accepted by every production gate'}</small></div>
    </section>
  );
}

function Timeline({ shot }: { shot: Shot }) {
  const all = [...shot.frames.flatMap((frame) => [frame.exposure_start_ms, frame.exposure_end_ms]), ...shot.timeline.radar_sample_ms, 0, shot.timeline.trigger_ms];
  const min = Math.min(...all); const max = Math.max(...all); const x = (value: number) => 4 + (value - min) / (max - min || 1) * 92;
  return <section className="timeline-card"><div className="section-title"><div><span className="eyebrow">TIME BASE</span><h2>Sensor timeline</h2></div><span>{min.toFixed(2)} to {max.toFixed(2)} ms</span></div><div className="timeline"><div className="timeline-axis"/>{shot.frames.map((frame) => <div key={frame.index} className={`exposure ${shot.timeline.used_frame_indices.includes(frame.index) ? 'used' : ''}`} style={{ left: `${x(frame.exposure_start_ms)}%`, width: `${Math.max(.6, x(frame.exposure_end_ms) - x(frame.exposure_start_ms))}%` }} title={`frame ${frame.index}`}/>) }{shot.timeline.radar_sample_ms.map((time, index) => <i key={`${time}-${index}`} className="radar-tick" style={{ left: `${x(time)}%` }}/>) }<i className="impact-tick" style={{ left: `${x(0)}%` }}/><i className="trigger-tick" style={{ left: `${x(shot.timeline.trigger_ms)}%` }}/></div><div className="timeline-key"><span><i className="used"/>used exposure</span><span><i className="radar"/>radar sample</span><span><i className="impact"/>OPS impact</span><span><i className="trigger"/>trigger</span></div></section>;
}

function Diagnostics({ shot }: { shot: Shot }) {
  return <section className="diagnostics-card"><div className="section-title"><div><span className="eyebrow">SOLVER INTERNALS</span><h2>Hypothesis diagnostics</h2></div><span className="hash">{shot.config_hash.slice(0, 12)}</span></div><div className="diagnostic-grid"><div><h3>Frame objective</h3><table><thead><tr><th>Frame</th><th>IoU</th><th>Margin</th><th>Condition</th></tr></thead><tbody>{shot.diagnostics.objective.map((row) => <tr key={row.frame_index}><td>F{row.frame_index}</td><td>{row.template_fit_iou.toFixed(3)}</td><td>{row.best_second_margin.toFixed(2)}</td><td>{row.condition.toFixed(1)}</td></tr>)}</tbody></table></div><div><h3>Quality + radar</h3><dl>{Object.entries({ ...shot.diagnostics.quality, ...shot.diagnostics.radar }).slice(0, 10).map(([key, value]) => <div key={key}><dt>{label(key)}</dt><dd>{typeof value === 'number' ? value.toFixed(3) : String(value)}</dd></div>)}</dl></div></div></section>;
}

export default function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [options, setOptions] = useState<StudioOptions | null>(null);
  const [controls, setControls] = useState<Controls | null>(null);
  const [shotIndex, setShotIndex] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { void loadInitial().then(([initial, loadedOptions]) => { setSession(initial); setOptions(loadedOptions); setControls(initial.controls); }).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason))); }, []);
  const shot = session?.shots[Math.min(shotIndex, session.shots.length - 1)] ?? null;
  const rejected = useMemo(() => session ? rejectionTotal(session.summary.rejections) : 0, [session]);
  const run = async () => {
    if (!controls) return; setBusy(true); setError(null);
    try { const next = await regenerate(controls); setSession(next); setShotIndex(0); }
    catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { setBusy(false); }
  };
  if (error && !session) return <main className="fatal"><h1>Studio could not load</h1><p>{error}</p></main>;
  if (!session || !options || !controls) return <main className="loading"><div className="mark">S</div><p>Loading committed Phase 4b session…</p></main>;
  return (
    <div className="studio-shell">
      <header><div className="brand"><span className="brand-mark">S</span><div><strong>SIM STUDIO</strong><small>Silhouette fusion workbench</small></div></div><div className="header-verdict"><span>PHASE-A DECISION</span><strong>{session.landing.ambient_verdict} · AMBIENT 500 µs</strong></div><a className="download" href={`/api/studio/session/${session.session_id}/download`}>Download artifact ↓</a></header>
      <div className="body-grid">
        <ControlsPanel controls={controls} options={options} busy={busy} onChange={setControls} onRun={() => void run()} />
        <main>
          {error && <div className="error-banner">Run failed: {error}</div>}
          <section className="landing">
            <div className="landing-heading"><div><span className="eyebrow">PHASE 4B · COMMITTED EVIDENCE</span><h1>Ambient is the hardware path.</h1><p>{session.landing.verdict_reason}. Strobe remains visible as a deferred, comparison-only fallback.</p></div><div className="hash-block"><span>EVALUATION HASH</span><code>{session.landing.evaluation_hash.slice(0, 16)}</code><small>{session.source.replaceAll('_', ' ')}</small></div></div>
            <CriteriaTable rows={session.landing.criteria} />
            <SpeedStrip rows={session.landing.club_speed_sweep} />
          </section>
          <section className="run-summary">
            <div className="summary-heading"><div><span className="eyebrow">CURRENT INTERACTIVE BATCH</span><h2>{label(String(session.controls.club))} · {label(String(session.controls.candidate))}</h2></div><span>{session.summary.n_attempted} deterministic shots</span></div>
            <div className="metrics"><Metric name="Availability" value={percent(session.summary.solve_rate)} detail={`${session.summary.n_ok} solved · ${rejected} rejected`} tone="availability"/><Metric name="Median vector error" value={mm(session.summary.median_mm)} detail="accepted shots"/><Metric name="p90 vector error" value={mm(session.summary.p90_mm)} detail="tail accuracy"/><Metric name="Rejection reasons" value={rejected ? String(rejected) : 'NONE'} detail={Object.entries(session.summary.rejections).map(([key, value]) => `${label(key)} ${value}`).join(' · ') || 'all shots accepted'} tone={rejected ? 'warning' : 'availability'}/></div>
            <div className="shot-picker">{session.shots.map((item, index) => <button type="button" key={item.seed} className={`${index === shotIndex ? 'active' : ''} ${item.ok ? 'solved' : 'rejected'}`} onClick={() => setShotIndex(index)}><span>#{index + 1}</span><strong>{item.ok ? mm(item.impact_error_mm) : 'REJECT'}</strong><small>{item.status}</small></button>)}</div>
          </section>
          {shot && <><div className="evidence-grid"><FrameInspector key={shot.seed} shot={shot}/><Clubface shot={shot}/></div><Timeline shot={shot}/><Diagnostics shot={shot}/></>}
          <footer>Research-only local application · real §4 artifacts · production classical solver · truth used for scoring and display only</footer>
        </main>
      </div>
    </div>
  );
}
