# Native PBC Unwrap Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans in the already selected Native session. Do not commit or push without a user request.

**Goal:** Add reversible Unwrap controls using MDAnalysis reconstruction and native PyMOL periodic processing.

**Architecture:** Derive source-bound previews on the compute target. A fixed isolated PyMOL helper processes consecutive frames with a two-state memory bound; only then are atoms and frames selected for the existing preview transport.

**Tech Stack:** Existing Python/MDAnalysis/OpenMM, operator-selected PyMOL/PyMOL2, existing JavaScript/Mol*.

**Spec:** `docs/superpowers/specs/2026-10-24-native-pbc-unwrap-design.md`

## Global Constraints

- Preserve raw scientific files, current uncommitted changes and the HPC-only data route.
- No dependency installation, schema paths/scripts from clients, scientific submission, automatic commit or push.
- Backward-compatible raw requests and manifests; explicit `pbc` enum and processing metadata for derived previews.
- Existing 120 s helper limit, source identity checks and preview budgets apply.

## Review Focus

- Missing/degenerate cells or unresolved ligand topology must retain the existing scene.
- Skipping raw frames before unwrap or restarting at a detail-window boundary must not change periodic images.
- A changed runtime must not reuse an older READY result under the same identity.
- Source switching, cancellation and helper timeout must discard late results and stop owned PyMOL children.
- Returning to raw or restoring a saved view must preserve source/time/appearance/camera binding.

### Task 1: Closed processing contracts and preview identity

**Files:** Python/JS visualization contracts, preview plan, service, worker, contract/identity tests.

**Interfaces:** `pbc` is absent/raw/unwrap. `plan_preview(source, request, pbc_runtime=None)` includes a validated processing identity only for unwrap. Manifests may include a closed `processing` record; legacy manifests stay readable.

- [x] Add failing contract tests for accepted modes, rejected arbitrary fields and processing metadata; add raw/unwrap/runtime identity separation tests.
- [x] Run the focused tests and record expected failures.
- [x] Implement normalization, runtime-aware request keys and worker binding.
- [x] Run Python/JS contracts and preview identity/source tests; require no failures.

### Task 2: Real native periodic processing and preview derivation

**Files:** New `visualization/pbc.py`, fixed `visualization/pymol_pbc_worker.py`; preview writer; source/bundle/runtime wiring; Python PBC tests.

**Interfaces:** `pbc_runtime()` returns private executable/fingerprint plus public processing versions. `PbcProcessor(source, runtime)` owns a fixed child and yields consecutive unwrapped coordinates with validated cells; `close()` stops the child. Native input/output uses bounded framed binary arrays, never executable content.

- [x] Add a real split-water/crossing test against the preview output and verify RED; no mocked native algorithm.
- [x] Implement known topology reconstruction, full-source traversal, two-state PyMOL processing and derived topology/XTC output.
- [x] Test triclinic/changing boxes, sparse preview frames, matching detail images, raw/source hashes, malformed cells, missing runtime, changed runtime and error cleanup.
- [x] Include the fixed helpers in the source bundle and pass the operator runtime setting only through the local compute environment; document remote configuration separately.
- [x] Require the focused suite to pass with the existing PyMOL executable; preserve failure logs.

### Task 3: Viewer controls and lifecycle

**Files:** Dock, renderer observation, dock tests and visualization documentation.

**Interfaces:** Buttons submit the existing prepare flow with `pbc`; displayed state comes from the loaded manifest. Requests, detailed windows and saved view state retain the mode. Rendering remains Mol*.

- [x] Add failing behavioral tests for mode switching, raw restoration and canceled/late results.
- [x] Implement `原始`/`Unwrap`, concise progress/errors, actual pressed state and box-only disabling.
- [x] Verify current time, camera, appearance and selection survive successful switches; failures keep the prior scene.
- [x] Run dock/player/appearance/theme/view-state regression tests.

### Task 4: Engineering validation and cleanup

**Files:** Existing build/engineering scripts; task-owned evidence under `.superpowers/sdd/2026-10-24-native-pbc-unwrap/`.

- [x] Run relevant Python/Node suites and frontend build; verify source-bundle allowlisting.
- [x] Start an isolated local DSH profile using existing runtimes, load a wrapped engineering water trajectory and verify native unwrap/raw buttons and playback.
- [x] Save source/processing identity, numerical checks, UI screenshot and test/build logs. Distinguish engineering data from scientific acceptance.
- [x] Review the task diff separately from preserved water-style work. Remove only owned disposable scratch after its processes stop; retain diagnostics and the active deliverable if still in use.
