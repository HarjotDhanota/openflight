# Static Range and Camera Exposure Implementation Plan

Status: active implementation plan; WIP committed, not yet fully validated

Date: 2026-09-26

Last corrected: 2026-09-28

Branch: `feat/tester-capture-pilot`

Clean base before implementation: `79092de5a553f0417beb402834e36ec694566dcf`

Last pushed planning baseline: `d07320a2c13003d948b74b979927756b1bba1e11`

This document is the handoff for completing the IWR6843 static-ball range and
camera exposure work. The partial implementation is committed in `8fa2768` and
the first next-agent handoff is committed in `d07320a`. Inspect those changes
before continuing; do not discard or rewrite them blindly. Re-check the
worktree because later planning or implementation edits may be uncommitted.

Do not push, open a PR, or merge this work into OpenFlight unless the owner
explicitly asks. Keep the work on the private feature branch as local,
reviewable commits.

## Read first

1. Read the repository-root `AGENTS.md` completely.
2. Read `ui/AGENTS.md` completely before changing UI code.
3. Read `docs/development/fusion-master-plan.md` and preserve its evidence and
   decision-log conventions.
4. Read this document completely before editing.
5. Inspect `git status`, `git diff`, and all untracked files before making a
   change.
6. If available on this machine, the external audit is at
   `C:\Users\harjo\Downloads\Untitled.md`.

Use `uv` for Python commands. Do not weaken tests, suppress failures, or claim
hardware qualification from Windows/offline tests.

## Corrected execution order

The numbered change sets are dependency ordered as follows:

1. Revalidate the committed WIP and freeze reproducible evidence.
2. Complete qualification schema v3.
3. Finish the guarded radar selector v2.
4. Integrate the static exposure search and durable backend lock, including a
   coarse bootstrap phase for scenes where the initial exposure cannot detect
   the ball.
5. Finalize API states, then make the UI render those states truthfully.
6. Add armed exposure enforcement and metric provenance only after the range
   and static-exposure contracts are stable.
7. Handle serial recovery as an independent lifecycle change.
8. Run the preregistered Pi qualification and frozen holdout.

Do not implement UI state assumptions ahead of the backend contract, and do
not let metric-provenance work expand the critical path for correct setup
range and exposure.

## Goal and non-negotiable behavior

The system must determine the tester's actual ball-to-radar distance from
qualified evidence. It must never substitute a fixed tee-distance default.
When evidence is absent, ambiguous, outside the qualified envelope, or tied to
the wrong estimator/exposure identities, the range must remain unresolved and
downstream estimators must be withheld while raw diagnostic evidence remains
available.

The static camera setup must find the lowest applied exposure and gain that
reliably show the reference ball, lock those settings for static ranging, and
report what the hardware actually applied. Static-ranging exposure is a
separate policy from swing-capture exposure and must not silently modify the
armed swing configuration.

Safety rules:

- No hard-coded or guessed tee distance.
- No accepted IWR result merely because one strong static reflector exists.
- Exactly one canonical IWR candidate may be emitted. Alternate peaks are
  diagnostic dictionaries only.
- Within-capture frame MAD is a scene-stability check, not a substitute for
  cross-capture environmental noise evidence.
- A missing ball, bad light, clipped ball, unstable scene, estimator identity
  mismatch, or unqualified placement must block promotion and production
  metrics.
- An explicitly labeled diagnostic mode may still preserve raw captures. It
  must not make the session or candidate look qualified.
- Never claim physical accuracy until the Raspberry Pi and real IWR6843/camera
  qualification matrix passes.

## Evidence that drove the change

The downloaded tester bundle is:

`C:\Users\harjo\Downloads\harjot-pilot-test-2-session-bundle-20260926T063433Z.zip`

Its three complete setup epochs expose the current selector failure:

| Epoch | Setup ID | Recorded v1 result | Relevant observation |
| --- | --- | --- | --- |
| E1 | `setup-20260925-fff56186f155` | Rejected bin 23; corrected candidate about 1.012118 m; score about 5.10 | Correctly did not promote, but no physical tape truth is stored in the bundle. |
| E2 | `setup-20260926-153ffb8aa4be` | Confidently accepted global bin 12; corrected about 0.496493 m; score about 27.2 | False-accept pattern near the lower search boundary; physical truth is not in the bundle. |
| E3 | `setup-20260926-b9f4dd8b3a75` | Confidently accepted bin 36; corrected about 1.621493 m; score about 19.17 | Operator independently reported placement near 1.01 m. Offline data also contains a secondary local peak near bin 23/about 1.012 m, but the report is not qualification truth. |

The capture-to-capture scale factors are approximately 0.998, 0.662, and
1.575. That invalidates simple absolute subtraction as a robust environmental
selector. The bundle also shows that a strong door/wall reflector can win even
when it is not the ball.

The checked-in fixtures intentionally record the operator's E3 distance only
as an external observation with `qualification_truth: false`. Do not convert it
to ground truth after the fact.

### 2026-09-28 Pi retest of the pushed WIP

The guided Arm 5 view on pushed commit `d07320a` reported a live 1280×800 frame
with mean 21.3 and brightest pixel 38, found no reference ball, drew no outline,
and correctly kept Save disabled. It displayed an IWR apparent-range diagnostic
of 1.153 m while stating that the radar candidate was not accepted for
provisional guidance and canonical range was withheld. No contemporaneous tape
truth was recorded for this observation, so 1.153 m is not an accuracy result.

Code inspection confirms that the guided range path calls `resolve_gain()`
once, starts `LiveView` with that fixed exposure/gain pair and never invokes the
new static exposure search or persists a static exposure lock. The
`static_exposure.py` module is currently used only for policy identity. This Pi
observation therefore confirms the known integration gap; it does not show
that the proposed exposure policy failed.

## Committed WIP inventory

Before commit `8fa2768`, these paths were modified or untracked:

```text
 M docs/development/fusion-master-plan.md
 M src/openflight/camera/reference_ball_range.py
 M src/openflight/camera/tester_server.py
 M src/openflight/iwr6843/range_evidence.py
 M src/openflight/iwr6843/static_capture.py
 M src/openflight/tee_range.py
 M tests/test_iwr6843_range_evidence.py
 M tests/test_tee_range.py
 M tests/test_tester_tee_range_flow.py
?? scripts/analysis/extract_static_range_regressions.py
?? src/openflight/camera/static_exposure.py
?? tests/fixtures/
```

They were committed together as an explicit WIP handoff. Re-run
`git status --short` and inspect `git diff 79092de..HEAD` because the worktree
and branch may have advanced. Do not assume any partial implementation is
correct merely because it is committed.

### Work already started

- The master-plan decision log has an entry describing the three epochs,
  false accepts, scale changes, missing truth, estimator identity gap, split
  exposure policies, and diagnostic raw-capture policy.
- `scripts/analysis/extract_static_range_regressions.py` reads the bundle ZIP,
  extracts the three raw captures, writes compact fixtures, records source and
  raw hashes, and supports `--check` regeneration.
- Three fixtures exist under `tests/fixtures/iwr6843_static_range/`.
- Range-evidence tests reproduce v1 behavior and require v2 not to accept the
  observed false bins 12 and 36.
- `range_evidence.py` contains a provisional v2 profile/selector with robust
  scale normalization, fractional and absolute gates, clustering, width,
  ambiguity, boundary, and stability checks.
- `static_capture.py` was switched to emit the v2 profile schema.
- Tee-range qualification was partially moved to schema v3 with camera range
  estimator, static exposure policy, and IWR estimator identities.
- Camera range-estimator identity helpers were started in
  `reference_ball_range.py`.
- `tester_server.py` has partial schema-v2 parsing, qualified search-range use,
  and identity checks.
- `static_exposure.py` contains an initial static exposure-lock data model,
  deterministic control ladder, optical assessment, selector, and lock writer.

### Critical limitations of that partial work

- The v2 radar constants are provisional and were only exercised against the
  captured fixtures and synthetic tests. They are not physically qualified.
- The new static exposure module is not integrated into the live camera/setup
  flow and has no focused tests yet.
- `tester_server.py` can currently compare exposure policy identity without
  proving that a real exposure lock was successfully applied. This must be
  corrected before any promotion path is allowed.
- UI truthfulness work has not started.
- Lighting failure handling, invalid qualification fail-soft behavior, manual
  camera eligibility, armed/disarmed exposure guards, per-metric optical
  quality, and serial recovery work remain incomplete.
- No complete test suite, UI test/build, final Ruff pass, or Pi hardware test
  has run after the latest edits.

## Change set 1: Freeze reproducible evidence

Review and finish the extractor and fixtures before tuning the selector.

Required work:

- Confirm each fixture is deterministically regenerated from the named bundle
  and that source/raw SHA-256 values are checked.
- Keep only evidence needed to reproduce the profile and decision; do not add
  large generated session artifacts to git.
- Add focused tests for corrupt/missing epochs, hash mismatch, and `--check`
  failure if they are not already covered.
- Preserve the v1 reproduction test so the historical bug is explicit.
- Ensure no fixture labels E1/E2/E3 with tape truth that the capture did not
  record.

Acceptance:

- Regeneration is byte-stable.
- The test suite demonstrates that v1 accepted the E2 bin-12 and E3 bin-36
  patterns.
- All provenance is auditable without the original session directory.

## Change set 2: Complete qualification schema v3

Qualification must bind the exact algorithms and policies used at runtime.

Required work:

- Finish schema v3 serialization, parsing, validation, tests, and all call
  sites.
- Bind at least:
  - camera range estimator SHA-256;
  - camera static exposure policy SHA-256 and policy purpose;
  - IWR static range estimator SHA-256;
  - the existing rig/camera/IWR identities and placement envelope.
- Keep `iwr_profile_sha256` as the capture/configuration identity. Do not
  repurpose it as the estimator identity.
- Add a deterministic golden qualification digest test.
- Treat legacy or malformed qualification as unqualified/unresolved in the
  tester workflow. It must not crash startup, silently migrate, or promote.
- Require exact identity equality, not compatible-prefix or version-only
  matching.

Acceptance:

- A single-byte policy/estimator change invalidates qualification.
- v2/invalid qualification leaves canonical range unresolved and records an
  explicit reason.
- All qualification construction helpers and fixtures use v3 intentionally.

## Change set 3: Finish radar selector v2

Audit the partial selector instead of treating its current thresholds as final.

Design requirements:

- Normalize background/current captures with a robust statistic that excludes
  candidate peaks and boundary artifacts.
- Use both fractional and absolute change evidence.
- Require temporal stability across frames, but use per-frame MAD only as a
  stability gate.
- Cluster contiguous candidate bins and apply width/shape checks.
- Reject or explicitly flag boundary candidates.
- Reject ambiguous scenes when the runner-up is too competitive.
- Derive the active search interval from the signed qualification/supported
  placement policy, including the declared bias convention. Do not derive it
  from the rig file alone.
- Emit one canonical candidate. Put runner-up/alternate peaks in diagnostic
  dictionaries that cannot be consumed as canonical measurements.
- Preserve v1 parsing only for review/replay compatibility; v1 must never be
  promotable under v3 qualification.
- Store all gates, normalization data, candidate clusters, rejection reasons,
  and estimator identity in durable evidence.

Required negative-test matrix:

- global amplitude scaling;
- static wall/door reflector stronger than the ball;
- candidate at each search boundary;
- two similarly strong candidates;
- broad/multipath peak;
- scene motion between frames;
- insufficient stable frames;
- weak valid-looking peak below absolute evidence;
- strong absolute but insignificant fractional change;
- qualified and unqualified search envelopes;
- E2 must not accept bin 12;
- E3 must not accept bin 36.

Do not write a test that asserts bin 23 is correct for E3 as physical truth.
It may be retained as a diagnostic observation only.

Acceptance:

- Offline and synthetic tests pass without weakening gates.
- The selector reports precise rejection reasons and exactly one or zero
  canonical candidates.
- Documentation calls the thresholds provisional until the hardware
  qualification stage.

## Change set 4: Integrate static exposure search and lock

Static ranging needs its own deterministic policy and durable lock artifact.
Do not reuse the swing exposure file or copy static settings into armed mode.

Required work:

- Finish the `static_exposure.py` data model, strict loader, validation, policy
  hash, atomic writer, and focused tests.
- Run the search only while disarmed/setup-only.
- Use a two-stage deterministic search:
  - bootstrap with bounded coarse exposure/gain steps until a plausible ball
    ROI can be found or the diagnostic ceiling is reached;
  - refine from the lowest nearby controls upward and select the lowest
    applied setting that passes every optical gate.
- The bootstrap stage may use whole-frame and candidate-region statistics only
  to obtain a detectable image. It must not qualify the ball or promote range.
- After each control request, read back applied camera metadata. Reject a step
  when the camera did not apply the requested controls within declared
  tolerance.
- Evaluate the actual ball ROI, not frame mean alone. At minimum gate:
  - ball detection confidence and minimum pixel diameter;
  - signal above black floor;
  - local ball/background contrast;
  - edge-gradient strength;
  - highlight/shadow clipping;
  - temporal stability over consecutive frames.
- Choose the lowest applied setting that passes all gates for the required
  number of stable frames.
- Persist requested/applied controls, sensor mode, ROI, optical metrics,
  attempts, pass/fail reasons, camera identity, policy identity, and timestamp.
- Require the lock artifact, camera identity, estimator identity, and current
  setup epoch to agree before camera evidence is promotable.
- Invalidate the lock after camera restart, mode change, identity change, ball
  or rig movement, new setup epoch, or material lighting change.
- Keep thresholds provisional and diagnostic until the physical M3
  qualification matrix is complete.

Acceptance:

- Unit tests prove the lowest passing applied setting is selected.
- Tests cover initially invisible balls, bootstrap exhaustion, delayed control
  application, ignored controls, low contrast, clipping, unstable detection,
  camera restart, stale lock, and identity mismatch.
- Static exposure never changes the armed swing exposure configuration.

## Change set 5: Make camera, API and UI state truthful

Define and test the backend state contract after change set 4, then make the
tester page show whether the ball and applied controls are actually usable,
not just that a frame exists.

Required work:

- Separate these states in the API and UI:
  - camera unavailable;
  - warming;
  - live but ball not found;
  - ball found but light/contrast/clipping failed;
  - static exposure bootstrap/search in progress;
  - exposure locked and applied;
  - camera range accepted/rejected/unqualified;
  - IWR apparent range observed but rejected;
  - canonical fused range accepted/withheld.
- Finalize one backend representation and reason-code vocabulary before wiring
  UI components. Do not duplicate state derivation in the frontend.
- Never render a rejected radar number as though it were a usable range. It may
  be shown in a clearly labeled diagnostics area with its reason.
- Overlay the detected ball outline and ROI only when detection is real. Avoid
  a decorative or stale outline.
- Show requested and applied exposure/gain values when they differ.
- Fix manual-camera eligibility so the backend and UI agree about whether a
  camera can contribute to canonical range.
- Normal tester mode must block save/promotion/production metrics when optical
  gates fail. An advanced diagnostic action may save raw evidence if it is
  explicitly labeled unqualified.
- Preserve accessibility, keyboard behavior, and useful error text.

Required UI coverage:

- backend contract and component/state tests for every state above;
- Playwright coverage for successful lock, missing ball, insufficient light,
  clipping, applied-control mismatch, rejected IWR, and withheld fusion;
- viewport coverage at 800x400, 800x480, and 1024x600;
- no clipped critical controls or hidden rejection explanations.

## Change set 6: Enforce armed exposure and metric provenance

Start this change only after change sets 1-5 have stable range, exposure-lock,
and API contracts. It must respect the distinction between production capture
and disarmed research capture without expanding the setup-critical path.

Required work:

- Enforce the qualified exposure ceiling only while armed for production.
- Permit longer exposures for disarmed setup/study captures when explicitly
  labeled diagnostic and prevented from entering production metrics.
- Do not make 300 microseconds a universal default or universal hard limit.
  Any ceiling must come from an active, qualified policy/profile.
- Prevent study-ladder or static-exposure choices from leaking into the next
  armed shot.
- Attach optical-quality evidence and reason codes to metrics that already
  exist and depend on camera pixels.
- Do not invent face-angle accuracy thresholds or advertise new production
  metrics in this change set.
- Reject/withhold affected metrics when exposure, blur, clipping, pixel scale,
  or estimator identity falls outside the qualified envelope.

Acceptance:

- Tests prove disarmed diagnostic freedom and armed production enforcement.
- Tests prove a study/static exposure cannot survive an arm transition unless
  the armed profile independently requests and verifies it.
- Metric payloads expose useful optical quality and withholding reasons.

## Change set 7: Treat serial recovery as a separate lifecycle fix

The CP2105/IWR CLI sometimes fails to answer until the user presses RESET. Do
not mix an unvalidated USB-reset workaround into the range-selector change.

Required work:

- Record port identity, probe attempts, open/close state, timeouts, firmware
  response, and operator reset action in durable preflight evidence.
- Audit ownership so no stale process or descriptor holds the Enhanced/if00 CLI
  port.
- Add repeated close/open lifecycle tests using a fake serial device.
- On the Pi, run a single-session experiment covering cold boot, application
  restart, failed preflight, operator reset, and subsequent restart.
- Improve the recovery message so it tells the operator exactly which port and
  action are required.
- Do not add automatic USB unbind/rebind, power cycling, or board reset until a
  real Pi experiment proves it is safe and necessary.

Acceptance:

- Software lifecycle bugs are reproduced and fixed in automated tests.
- Remaining firmware/board reset behavior is documented as a hardware
  limitation with captured evidence, not hidden behind a retry loop.

## Change set 8: Hardware qualification and promotion gates

No preceding change makes the system production-accurate by itself.

Required Pi/physical matrix:

- multiple tape-measured ball distances spanning the supported envelope;
- door/wall/background reflectors at several positions;
- low, normal, bright, backlit, and sunlight-contaminated camera scenes;
- multiple ball colors/conditions that are actually supported;
- camera/radar cold start and warm restart;
- different radar amplitudes and plausible multipath layouts;
- repeated captures at every condition;
- separate development and holdout placements/environments.

Process:

1. Run a downstream sensitivity analysis and obtain owner approval for the
   numeric gates before collecting qualification data.
2. Freeze estimator and exposure-policy hashes before collecting the holdout.
3. Store tape truth contemporaneously in the session evidence.
4. Tune only on the development set.
5. Run the untouched holdout once with frozen identities.
6. Record false accepts, false rejects, range error, optical-gate failures, and
   all withheld reasons.
7. Sign qualification only for the tested hardware, firmware, camera mode,
   placement envelope, estimator hashes, and exposure policy.

Provisional preregistration target, to be accepted or revised before data
collection:

- Radar development: at least 5 tape-measured distances × 4 reflector layouts
  × 5 complete repeats for each supported mount/profile combination.
- Radar holdout: at least 3 unseen distances × 3 unseen reflector layouts × 5
  complete repeats, collected only after hashes are frozen.
- Camera development: at least 4 supported lighting regimes × 5 complete
  searches for each supported camera mode, including dim, normal, bright and
  backlit scenes.
- Camera holdout: at least 3 unseen scenes × 5 complete searches per supported
  mode after the policy hash is frozen.
- No false promotion in no-ball, ambiguous, identity-mismatch,
  out-of-envelope, or holdout cases.
- At least 90% accepted-range availability inside the declared operating
  envelope, with median absolute range error no greater than 0.025 m and P95
  no greater than 0.050 m.
- Applied exposure/gain identity must match on 100% of promoted camera
  observations; supported-lighting ball/optical availability must be at least
  95%, with zero no-ball promotions.

These values are engineering targets, not existing results. If downstream
launch-angle sensitivity or initial development data shows that a target is
inappropriate, revise and record it before freezing the holdout. Never tune a
gate after seeing the holdout outcome.

Promotion must fail closed when any identity or envelope differs. Report real
accuracy and failure rates; do not turn a small test set into a broader claim.

## Deferred research workstream: IWR6843 spin feasibility

The channel-aware micro-Doppler and differential-phase proposal is documented
separately in
[IWR6843 spin feasibility study](2026-09-28-iwr6843-spin-feasibility.md).
It remains an offline M4 research track after the range/exposure critical path.
Total spin, OPS harmonic disambiguation and spin axis have separate gates;
differential array phase is not treated as a direct spin measurement.

## Recommended local commit boundaries

Keep these as separate reviewable commits, not one large commit:

1. `test: freeze static range regression evidence`
2. `feat: bind tee qualification estimator identities`
3. `feat: add guarded static radar selector v2`
4. `feat: add static camera exposure lock`
5. `feat: report truthful tester range states`
6. `feat: enforce armed optical policy`
7. `fix: harden IWR serial lifecycle`
8. `docs: record hardware qualification results` only after real hardware work

Keep IWR spin research in a later, separate branch/commit series after the
range/exposure critical path. Do not fold an unvalidated spin estimator into
these commits.

Do not create empty commits just to match this list. If a boundary cannot pass
its focused tests independently, fix the dependency or document why two
adjacent boundaries must be combined.

## Resume and validation commands

Start by preserving evidence about the current worktree:

```powershell
cd C:\Users\harjo\Desktop\Coding\OpenFlight\openflight-tester-pilot
git status --short
git diff --check
git diff --stat
git diff
```

Regenerate/check the captured fixtures:

```powershell
uv run --extra camera python scripts/analysis/extract_static_range_regressions.py --bundle "C:\Users\harjo\Downloads\harjot-pilot-test-2-session-bundle-20260926T063433Z.zip" --output-dir tests/fixtures/iwr6843_static_range --check
```

The last known passing focused radar run was:

```powershell
uv run --extra camera pytest tests/test_iwr6843_range_evidence.py -q -p no:cacheprovider
```

It reported 23 passed before later camera/server edits. Treat every other
result as stale. A combined tee-range test command was aborted by the crashed
session and therefore did not pass:

```powershell
uv run --extra camera pytest tests/test_tee_range.py tests/test_tester_tee_range_flow.py -q -p no:cacheprovider
```

Before committing each change set, run its focused tests plus Ruff. Before the
final handoff, run at least:

```powershell
uv run ruff check src scripts tests
uv run ruff format --check src scripts tests
uv run --extra camera pytest tests/test_iwr6843_range_evidence.py tests/test_tee_range.py tests/test_tester_tee_range_flow.py tests/test_tester_server.py tests/test_server.py -q -p no:cacheprovider
git diff --check
```

Also run every new focused test file, the repository-required camera/fusion
groups, and the UI lint/build/Playwright gates described by `ui/AGENTS.md`.
Never interrupt a healthy long-running test merely to report progress. If a
test aborts because the agent or shell crashes, report it as aborted, not
passed.

## Completion checklist

The implementation is not complete until all of the following are true:

- [ ] Fixtures regenerate deterministically with verified hashes.
- [ ] Qualification v3 binds exact radar, camera estimator, and exposure policy
      identities.
- [ ] Invalid/legacy qualification fails soft to unresolved and cannot promote.
- [ ] Radar v2 rejects the known E2/E3 false reflectors and the full synthetic
      negative matrix.
- [ ] Alternate peaks cannot become canonical measurements.
- [ ] Camera bootstrap can reveal an initially invisible ball without treating
      bootstrap evidence as qualification.
- [ ] Camera setup selects and proves the lowest applied passing setting.
- [ ] Static exposure is isolated from armed swing exposure.
- [ ] UI and durable evidence distinguish observed, rejected, accepted,
      unqualified, and withheld values.
- [ ] Normal mode blocks promotion on missing ball or bad optical quality while
      diagnostic mode can preserve clearly unqualified raw evidence.
- [ ] Existing camera-dependent metrics carry optical-quality provenance and
      fail closed outside qualification.
- [ ] Serial lifecycle fix is separately tested and Pi evidence is captured.
- [ ] Focused tests, broader Python tests, Ruff, formatting, UI lint/build, and
      Playwright pass without weakened gates.
- [ ] Master-plan checkpoint and decision log reflect what was actually proven.
- [ ] Numeric qualification gates and sample counts were frozen before the
      holdout, and the holdout was not used for tuning.
- [ ] No hardware accuracy claim is made until the Pi holdout passes.
- [ ] No push or PR occurs without explicit owner approval.

## Suggested prompt for the implementing agent

> Continue the partially implemented static-range and camera-exposure work in
> `C:\Users\harjo\Desktop\Coding\OpenFlight\openflight-tester-pilot`. Read the
> root `AGENTS.md`, `ui/AGENTS.md`, and
> `docs/development/2026-09-26-static-range-camera-exposure-implementation-plan.md`
> completely before editing. The partial implementation is committed; inspect
> `git status` and `git diff 79092de..HEAD` before changing it, and preserve its
> evidence instead of resetting it. Complete every change set in the corrected
> dependency order as small local commits, with failing regression tests first
> where applicable. Keep the IWR spin work offline and separate from the
> range/exposure critical path. Do not push, open a PR, merge into OpenFlight,
> weaken gates, or claim hardware qualification. Run all specified focused and
> broader test, lint, format, build, and Playwright gates, and update the fusion
> master plan with only evidence-backed claims. Stop before any remote operation
> and report exact commits, tests, remaining Pi work, and unresolved risks.
