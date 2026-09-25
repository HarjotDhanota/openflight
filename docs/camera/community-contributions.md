# Community contribution packages

Raw tester exports are local evidence. They are never uploaded by FlightWeb or
by the contribution-package tool. Build a package only after a contributor has
given the documented consent for the intended audience.

Create a metadata file with no name, email, address, machine name, or local
path. `contributor_id` is a pseudonym, not an account identifier.

```json
{
  "visibility": "private-review",
  "consent_version": "community-data-v1",
  "consented_at": "2026-09-24T18:00:00+00:00",
  "contributor_id": "tester_01",
  "license": "CC-BY-4.0"
}
```

Build and verify a local package from an existing session export:

```bash
uv run python scripts/analysis/build_contribution_package.py build \
  --export exported-session --metadata consent.json --output contribution.zip
uv run python scripts/analysis/build_contribution_package.py verify contribution.zip
```

The ZIP contains a canonical `contribution_manifest.json` that inventories every
included file with its path, byte size, SHA-256, and content class. Its detached
`.sha256` file identifies the immutable archive. A changed or undeclared member
causes verification to fail.

`private-review` is the default sharing choice and includes the selected export
evidence. `public-derived` includes only `shots.csv` and `excluded_shots.csv`;
it excludes session logs, raw I/Q, radar dumps, camera frames, ledgers, source
archives, and local metadata. Publishing still requires human review for
re-identification risk, license suitability, and consent withdrawal handling.

The package records source-session, export-contract, rig, and runtime hashes
when the export contains them. It supports reproducible offline analysis, but
does not prove physical accuracy, calibration, contributor identity, consent
quality, or completeness of recorded attempts.
