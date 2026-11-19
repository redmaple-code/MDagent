# Native PBC Unwrap

## Intent and confirmed approach

Add `Unwrap` and `原始` controls to the existing DSH structure/trajectory window. The user confirmed both broken molecules within a frame and periodic jumps during playback, and approved MDAnalysis molecule reconstruction followed by native PyMOL `cmd.pbc_unwrap(..., bymol=1)`.

The alternative of PyMOL alone was rejected for this scope: a real three-atom probe leaves the already split first frame unchanged. Browser-side image arithmetic would not use the requested native engine. Processing runs on the bound compute target, before atom selection and preview frame sampling; the existing HPC-to-local transfer remains unchanged.

## Processing and identity

- Requests may add `pbc: "unwrap"` or `"raw"`; absent means raw for existing clients. No paths, scripts or arbitrary commands are accepted from the browser.
- Recover bonds from the registered PDB's explicit/standard OpenMM topology, require matching atom identities/order, and reject an unresolved molecular topology. Do not guess ligand bonds by proximity.
- Validate every source cell and finite coordinate array. MDAnalysis reconstructs bonded fragments with `reference=None`; this does not center or wrap the molecules.
- Feed every stored source frame from frame zero through the requested end to an isolated PyMOL process. Retain only the previous unwrapped frame plus the current frame. Set each state's own box and angles, then invoke native `pbc_unwrap`. No smoothing or interpolation.
- Select the requested atoms/frames only after processing. Detail windows use the same prefix and produce the same images as the overview. Explicit source-time gaps fail instead of silently bridging missing data.
- Publish a separate immutable preview. Its identity includes the processing mode, implementation, reconstruction versions and native runtime fingerprint. The manifest identifies the processing; the original source hash and frame/time mapping stay bound.
- Original structures, trajectories, checkpoints and accepted manifests are never rewritten. Switching back reopens a raw preview and preserves time, appearance, selection and camera.

## Runtime and limits

Reuse existing environments. PyMOL may run in the compute Python or a separate operator-configured `BIO_PREVIEW_PYMOL_PYTHON`; that executable is never client supplied. Pass only the required operator setting to local compute helpers. Remote operators must configure the compute-side runtime separately; no installation or deployment is implicit.

Validate native API availability and fingerprint the engine before queuing; the worker verifies the same runtime. Missing PyMOL, invalid cells, unresolved bonds, source changes, cancellation and timeouts must keep the current display intact and produce a concise error. The existing 120 s helper limit and preview disk/transfer budgets remain. Child-process cleanup must run on cancellation and errors.

## UI and validation

Two compact buttons beside the playback controls: `原始` and `Unwrap`, with pressed state from the displayed manifest. Disable processing for a box-only view and while the same action is pending. Source changes cancel late results; saved view requests retain the mode. No model call or coordinate relay through the DSH host is added.

Use real native PyMOL tests for split-water repair, periodic crossings, triclinic/variable boxes, skipped preview frames and detail-window consistency. Verify source hashes, raw/unwrap cache separation, missing-runtime and cancellation behavior. Build the existing frontend and verify both buttons in the native DSH engineering profile. Real HPC/SSH and the user's scientific trajectory remain separate acceptance layers.

## Scope boundaries

This adds a display-derived trajectory, not a replacement for formal diffusion analysis. It cannot recover missing stored motion beyond the information in the original sampling. No new LAMMPS reader, permanent export, camera recentering or dependency installation is included. Existing uncommitted water-style changes are preserved. No commit or push without a further user request.
