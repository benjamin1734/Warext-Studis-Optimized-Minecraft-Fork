# Warext Server Engine 26.3 exp.19

Experimental high-performance Minecraft server engine build for the 26.3 line.

## Experimental Moonrise direct ticking-chunk lookups
- Add `performance.chunk.direct-ticking-set-lookups.enabled`, **disabled by default**.
- Maintain primitive-long block-ticking and entity-ticking chunk position sets from Moonrise chunk-holder status transitions.
- Remove entries whenever a chunk holder is removed, preventing stale ticking status.
- When enabled, short-circuit the DistanceManager, ServerChunkCache and ServerLevel ticking-range checks before the usual chunk-holder map lookup.
- When disabled, retain the original Paper/Moonrise holder-based lookup behavior.
- Populate the optional lookup sets only while enabled. Restart after changing this option.
- The experimental cache requires correct main-tick-thread use of the Moonrise lifecycle; it is not yet recommended for a busy public production server without stress testing.

This adapts the core idea of "Moonrise: Direct Set Lookups for Ticking Chunks" from the unmerged Leaf PR #946; it is **not** a blanket port of that PR.

## Testing
- Build the runnable Paperclip JAR.
- Smoke-test `balanced`, `extreme`, and `pregeneration` profiles.
- Validate deferred-container loading, distant End worldgen, and normal-world Aquifer/compact-palette save-reload.
- Explicitly enable the experimental direct ticking cache in a separate server boot; force-load and release a distant chunk ticket, save and shut down.
- Validate real startup, chunk ticket command completion, no exception/corruption logs and clean shutdown.
- Publish the release JAR, SHA256 sums and pinned upstream revision only after all CI checks pass.

## Compatibility
- Current Leaf `ver/26.3` base: `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.
- No newer upstream 26.3 commit was available at the time of this release.
- Previous experimental optimizations remain present without changing their defaults.

This is a prerelease. A CI smoke test is not a full-world gameplay parity or sustained 4-vCPU benchmark.
