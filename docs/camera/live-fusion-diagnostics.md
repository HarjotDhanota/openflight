# Tester fusion diagnostics

The tester diagnostic view shows results from the existing shot pipeline. It
does not run another estimator, acquire another sensor stream or select a new
calibration candidate. Its purpose is to expose pending work, fallback results,
rejected candidates and the evidence behind each displayed number.

## Open the view

Start the tester normally with `scripts/start-tester.sh`, then choose **View
fusion diagnostics** from the tester page (or open `/fusion-diagnostics.html`
on the same server). Enter your tester ID and choose the exact saved run. An
active run appears once its directory exists; use **Refresh** to rediscover it.
The selected run updates about once per second while the page is visible.
Session logging must be enabled to preserve these snapshots.

For a hidden block, press **Start hidden-metric block** before selecting a run.
After collection, press **End block**, then **Reveal metrics**. These controls
do not start or stop acquisition. The block setting persists in this browser;
a different browser has its own setting.

## Identity and completion

Select the saved run corresponding to the active capture. A shot is identified
by its logging-session UUID and shot number; its diagnostic revision identifies
an update to that same shot. Shot number alone is insufficient because a new
session can restart numbering at one. Changing runs must clear the old display.

Provisional results come from the existing OPS callback. Terminal results come
from the existing ordered finalization step, including its timeout and queue
fallback paths. Terminal means the processing step ended. It does not mean all
sensors contributed, all metrics are available, or a metric passed an accuracy
gate. Missing records from older sessions remain explicitly unavailable.

A closed session with an unfinished diagnostic record does not prove that the
shot completed. A disconnected browser cannot establish processing completion
either. The display must retain those distinctions after reconnecting.

## Reading the numbers

Each metric carries a unit, source, availability status and validation label.
Estimated launch-angle fallbacks remain estimates. Experimental camera/radar
delivery stays experimental even when its internal quality checks accept it.
Mock results must remain identifiable. Zero is a valid value; unavailable values
must not be replaced with zero.
Rejected candidates may retain a numerical value for inspection; their status
and recorded rejection reason remain visible. All current outputs are
unvalidated, including those whose processing outcome is `complete`.

OPS radial ball speed and total speed have different definitions. The display
identifies the recorded contract and does not silently reinterpret one as the
other. An optional single-window line-of-sight total-speed candidate exists
only in offline replay. Canonical live OPS speed remains radial, and live use
or promotion of the candidate is withheld pending independent geometry, timing,
and reference evidence.

Final discrepancies use only compatible evidence retained by the pipeline.
The camera/IWR horizontal comparison is model disagreement, not error against
an independent reference. Camera-assisted estimates can share radar range and
OPS constraints, so agreement does not independently validate accuracy. A
retained numerical candidate whose quality gate rejected it must not appear as
an accepted comparison. The discrepancy cards cover the recorded camera/IWR
horizontal-bearing difference and the pixel distance between the scene and
impact reference-ball detectors. The detector card states whether those two
observations agreed; the bounded candidate coordinates, rejection reasons and
selection provenance remain in the shot's `camera_fusion_processing` record.
Club candidates are shown separately; the view does not compute club deltas
without an established physical-point, time and measurement-definition contract.

## Hidden-metric testing blocks

The view can hide metric values and discrepancies until the operator explicitly
ends a testing block and reveals them. Processing state and recorded-shot counts can
remain visible. Reloading, reconnecting or changing runs must not silently
reveal a hidden block. This is a display feature to reduce feedback during
collection; it does not change processing or provide access control over files.
Recorded shots are not a count of all physical swings. Use the independent
operator attempt ledger to preserve swings that generated no shot event.

## Log reading and retention

The read-only `/api/tester/diagnostics` endpoint uses the tester, arm and exact
run scope. It incrementally reads at most 1 MiB of log content per request,
plus small file-identity checks. It retains the latest 100 shot identities per
session within a window of 64 session files. The response reports incomplete
indexing and truncated history. The original logs remain intact for export.
Lines exceeding 256 KiB are skipped; an unfinished trailing line waits for a
later poll. These limits apply to this display, not the recorded evidence.

Snapshots use revision 1 for pending processing and revision 2 for terminal
results. Session UUID checks prevent late callbacks from writing into a new
session. Legacy logs without diagnostic records report unavailable. A network
error clears displayed values and retries; reconnecting restores records from
the selected run without assuming that pending work completed.

## Evidence and remaining gates

Diagnostic records are additional session evidence and travel with the session
logs. Browser/API tests establish software behavior. Real capture latency,
dropped-frame behavior and the full diagnostic workload still require paired
hardware tests. Showing a live number does not qualify optics, timing, radar
association or accuracy against a reference launch monitor.

Use the [saved-track review](recorded-track-comparison.md) to inspect individual
pixel observations, and the [master plan](../development/fusion-master-plan.md)
for the remaining calibration, replay and release gates.
