# Warext Server Engine 26.3 exp.13

Experimental high-performance server engine build for the 26.3 line.

## Deferred container item decoding
- Add an opt-in `performance.container-item-lazy-loading` module, disabled by default.
- Defer decoding of raw `Items` NBT for chests, trapped chests, barrels and shulker boxes until inventory contents are actually accessed.
- Preserve raw item NBT on save when a container was loaded but never opened/accessed, avoiding an unnecessary decode/re-encode cycle.
- Retain only the raw `Items` field instead of the full block-entity NBT tree.
- Lithium cache-invalidation probes can inspect the backing list without forcing deferred contents to decode.
- Normal inventory access, Bukkit-facing `getContents()`, hopper/Lithium access and first mutation all force a real decode before use.
- Feature remains experimental because it trades lower chunk-load decode cost for temporary retention of raw item NBT.

Original optimization concept/patch: HaHaWTH, Leaf PR #928; adapted for Warext Server Engine 26.3 with raw-field presence checks and current Lithium/block-entity integration.

## Validation
- Balanced real-server smoke test.
- Extreme real-server smoke test.
- Dedicated deferred-container smoke test:
  - generate Warext config and enable `container-item-lazy-loading`;
  - create and save a chest with an item;
  - restart with lazy loading enabled;
  - verify the experimental module is active;
  - access/mutate the loaded chest to force first-access decode;
  - flush/save and stop cleanly without load/save errors.

## Existing performance/correctness layers retained
- Async path one-time prepare/trim lifecycle and callback-allocation cleanup.
- Brain.PATH, fallback-path, POI/HOME/nearest-bed and Paper Pathfinder API correctness.
- Generator-scoped evaluator pools and mounted-mob malus semantics.
- FastBitRadixSort entity-distance hot-path optimization.
- ThreadLocal light-buffer retention cleanup.
- Reusable tracker, collision, spawn/despawn and NBT serialization buffers.

## Upstream base
- Leaf 26.3 pinned at `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.

## Publishing
- Publish `Warext-Server-Engine-26.3.jar`, `SHA256SUMS.txt`, and `UPSTREAM_COMMIT.txt` only after all smoke tests pass.
- Fail the workflow if the GitHub Release does not contain the runnable `.jar`.

This release remains a prerelease while the Leaf 26.3 line is still being stabilized.
