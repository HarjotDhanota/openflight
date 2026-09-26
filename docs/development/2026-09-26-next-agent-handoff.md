# Next-Agent Handoff: Tester Static Range and Camera Exposure

Date: 2026-09-26

Branch: `feat/tester-capture-pilot`

Starting commit: `8fa27688d1f4881e937e3d78450e5cd3a53e4cfb`

This is a continuation handoff, not a completion report. The branch contains a
partially implemented static-range selector, qualification schema changes,
camera exposure-policy scaffolding, captured regression fixtures, and a
detailed implementation plan. The current code must not be treated as
production-ready or physically qualified.

## Owner's objective

OpenFlight must determine the tester's actual ball-to-radar distance from
qualified camera and IWR6843 evidence. It must not use a fixed or guessed
distance. In particular, 1.575 m and 2.34 m must never act as fallback tee
distances.

The owner also wants the camera to use the lowest exposure/gain combination
that reliably shows the reference ball and preserves enough pixel quality for
camera-derived measurements. Static setup exposure and armed swing exposure
must remain separate policies.

The work is still private feature-branch work. Do not open a PR against
OpenFlight, merge it, or claim production readiness without explicit owner
approval.

## Read these files before editing

1. Repository-root `AGENTS.md`.
2. `ui/AGENTS.md` before any UI change.
3. `docs/development/fusion-master-plan.md`.
4. `docs/development/2026-09-26-static-range-camera-exposure-implementation-plan.md`.
5. This handoff.

The implementation plan is authoritative for the eight change sets,
acceptance gates, negative-test matrix, recommended commit boundaries, and Pi
qualification procedure. This file summarizes the current state and immediate
next actions; it does not replace that plan.

## Why this work exists

The tester displayed an IWR apparent range of 1.621 m while the operator placed
the ball at approximately 1.01 m. The camera also failed to detect/outline the
ball in a dark scene. Offline review found that a door or wall behind the ball
could dominate the static radar difference and that capture-to-capture power
scaling varied significantly.

The captured session contains three complete setup epochs:

| Epoch | Setup ID | Historical v1 behavior |
| --- | --- | --- |
| E1 | `setup-20260925-fff56186f155` | Rejected bin 23, corresponding to about 1.012 m after correction. |
| E2 | `setup-20260926-153ffb8aa4be` | Incorrectly accepted boundary-adjacent bin 12, about 0.496 m. |
| E3 | `setup-20260926-b9f4dd8b3a75` | Incorrectly accepted bin 36, about 1.621 m; a secondary peak existed near bin 23. |

Only E3 has an external operator report near 1.01 m. It is not contemporaneous
tape truth in the bundle and must remain labeled `qualification_truth: false`.
Do not write a test that declares bin 23 physically correct based only on this
report.

## Evidence locations

Downloaded bundle on the owner's Windows machine:

```text
C:\Users\harjo\Downloads\harjot-pilot-test-2-session-bundle-20260926T063433Z.zip
```

Regression extractor:

```text
scripts/analysis/extract_static_range_regressions.py
```

Checked-in fixtures:

```text
tests/fixtures/iwr6843_static_range/
```

An external Claude audit may be available locally at:

```text
C:\Users\harjo\Downloads\Untitled.md
```

The repo plan incorporates the audit's useful corrections, so absence of that
external file is not a blocker.

## What is already in commit `8fa2768`

- Three deterministic captured-range fixtures and an extraction/check script.
- Regression tests that reproduce the historical v1 behavior.
- A provisional `StaticRangeProfileV2` and selector with robust normalization,
  fractional and absolute evidence, clustering, boundary, ambiguity, width,
  and temporal-stability gates.
- Static capture switched to the v2 profile schema.
- Partial tee-range qualification schema v3 fields for exact camera estimator,
  static exposure policy, and IWR estimator identities.
- Camera range-estimator policy identity helpers.
- Partial tester-server parsing, qualified search-envelope use, and identity
  checks.
- A new but unintegrated `camera/static_exposure.py` policy/lock scaffold.
- A fusion-master-plan decision-log entry.
- The detailed implementation plan referenced above.

These are partial changes. In particular, the server can compare a static
exposure-policy identity without yet proving that an exposure lock was
successfully applied. That path must remain non-promotable until the lock is
integrated and verified.

## Known incomplete work

- Complete schema-v3 construction, parsing, golden digest, strict identity
  binding, and fail-soft handling of legacy/invalid qualification.
- Audit the provisional radar-v2 implementation and add the complete negative
  synthetic matrix.
- Integrate static exposure search into the disarmed guided setup flow.
- Persist, load, validate, invalidate, and prove applied exposure locks.
- Make API/UI states truthful for ball-not-found, lighting failure, clipping,
  rejected radar evidence, unresolved fusion, and requested/applied controls.
- Keep diagnostic raw capture available while blocking normal promotion and
  production metrics.
- Separate static/study exposure from the armed swing profile and enforce the
  qualified ceiling only in armed production mode.
- Add optical-quality provenance to existing camera-derived metrics without
  inventing new metric-accuracy claims.
- Address the CP2105/IWR serial-reset lifecycle as a separate change set.
- Run real Pi hardware qualification and a frozen holdout matrix.

## Exact safety decisions to preserve

- Unresolved means `None`, raw-only, and estimators withheld. Never substitute
  a numeric default.
- Qualification must bind exact estimator and exposure-policy hashes.
- `iwr_profile_sha256` remains the radar capture/configuration identity; do not
  repurpose it as the selector identity.
- The supported search interval comes from qualification/placement policy, not
  from the rig file alone.
- Exactly one IWR candidate can be canonical. Alternate peaks are diagnostics
  only.
- Frame MAD is a within-capture scene-stability gate, not cross-capture noise
  evidence.
- Bad light or missing ball blocks normal promotion, but an explicitly labeled
  diagnostic action may preserve raw evidence.
- Do not impose 300 microseconds as a universal exposure default or limit. Any
  armed ceiling comes from the qualified active profile.
- Do not copy static exposure settings into the next armed swing capture.
- Do not add automatic USB reset/unbind behavior without a controlled Pi
  experiment.

## Verification status at handoff

Before commit `8fa2768`:

- Pre-commit `ruff`, `ruff-format`, and `pylint` passed.
- `git diff --check` was clean after removing Markdown trailing whitespace.
- An earlier radar-only focused run reported 23 passed before later server and
  camera edits.
- A later combined focused run reported 78 passed and 30 setup errors because
  the tester-server estimator identity hook had been removed. The hook was
  restored and used in the runtime identity check.
- Two attempts to rerun the combined focused suite after that fix were aborted
  by the agent/session crash. They are not passing results.
- No final full Python suite, UI lint/build, Playwright run, or Pi hardware test
  has completed against `8fa2768`.

Therefore, begin by validating the committed state. Do not assume the restored
hook was sufficient.

## Immediate continuation sequence

1. Confirm the branch and remote state:

   ```powershell
   cd C:\Users\harjo\Desktop\Coding\OpenFlight\openflight-tester-pilot
   git status --short
   git branch --show-current
   git rev-parse HEAD
   git fetch origin
   git rev-list --left-right --count HEAD...origin/feat/tester-capture-pilot
   ```

2. Verify the fixture extractor:

   ```powershell
   uv run --extra camera python scripts/analysis/extract_static_range_regressions.py --bundle "C:\Users\harjo\Downloads\harjot-pilot-test-2-session-bundle-20260926T063433Z.zip" --output-dir tests/fixtures/iwr6843_static_range --check
   ```

3. Rerun the focused suite that was interrupted:

   ```powershell
   uv run --extra camera pytest tests/test_iwr6843_range_evidence.py tests/test_tee_range.py tests/test_tester_tee_range_flow.py -q -p no:cacheprovider
   ```

4. Fix real failures with focused regression tests. Do not weaken gates or
   rewrite captured fixtures to make tests pass.
5. Complete change sets 1 through 7 from the implementation plan as small,
   locally testable commits.
6. Run the full required Python and UI verification before asking for hardware
   testing.
7. Provide exact Pi commands only after the software gates pass and record all
   hardware outcomes in durable session artifacts.

## Required final reporting

When handing back to the owner, report:

- every new commit hash and message;
- exact tests/checks run and their results, including skips;
- whether local, origin, and GitHub hashes match;
- all remaining uncommitted or untracked files;
- what has and has not been validated on the Raspberry Pi;
- measured failure/accuracy results without extrapolation;
- any remaining promotion blockers;
- confirmation that no OpenFlight PR or merge was created unless explicitly
  authorized.

## Ready-to-use continuation prompt

> Continue the tester static-range and camera-exposure work on
> `feat/tester-capture-pilot` from commit
> `8fa27688d1f4881e937e3d78450e5cd3a53e4cfb`. First read the root `AGENTS.md`,
> `ui/AGENTS.md`,
> `docs/development/2026-09-26-next-agent-handoff.md`, and
> `docs/development/2026-09-26-static-range-camera-exposure-implementation-plan.md`
> completely. Treat the committed implementation as WIP, rerun the interrupted
> focused tests before relying on it, and complete all change sets with failing
> regressions first and small local commits. Do not use a fixed tee-distance
> fallback, weaken qualification or optical gates, claim hardware accuracy,
> open a PR, merge, or push to any branch other than the private feature branch
> without explicit owner approval. Preserve raw diagnostic evidence while
> keeping unqualified outputs non-promotable, and report exact tests, commits,
> remaining Pi work, and risks.
