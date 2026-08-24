# Silhouette Sim Studio

Research-only diagnostic UI for the committed Phase 4b evidence and interactive
generator/fusion runs. The API calls the Section 4 artifact writer and the same
`fusion.solve_shot` entry point evaluated in Phase 4b. It does not use or modify
the production Flask server.

The committed two-shot fixture is returned immediately on first load. Changing
a control and selecting **Regenerate + solve** creates fresh deterministic
artifacts in a temporary directory, solves them, and returns display diagnostics.
Truth is included only in the Studio scoring/overlay response; it is never an
input to the fusion package.

From the repository root, start the local API:

```powershell
uv run --group research --directory research python -m silhouette_poc.studio.api
```

For UI development, use a second terminal:

```powershell
Set-Location research/silhouette_poc/studio/web
npm ci
npm run dev
```

Vite proxies `/api` to `http://127.0.0.1:8765`. A production build is served by
the Python process at `http://127.0.0.1:8765`.

Required Studio verification:

```powershell
Set-Location research/silhouette_poc/studio/web
npm run lint
npm test
npm run build
```

Regenerate the small committed fixture after an intentional API or accepted
evaluation update:

```powershell
uv run --group research --directory research python -m silhouette_poc.studio.build_fixture
```
