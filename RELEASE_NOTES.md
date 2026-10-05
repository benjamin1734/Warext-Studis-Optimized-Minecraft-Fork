# Warext Server Engine 26.3 exp.8

Experimental high-performance server engine build for the 26.3 line.

## Async navigation lifecycle correctness
- Preserve partial paths instead of replacing every unreachable result with a random fallback.
- Keep the original target's `CANT_REACH_WALK_TARGET_SINCE` timer intact when a fallback route succeeds.
- Restart an async navigation request promptly when `WALK_TARGET` moves while the old path is still processing.
- Reset async fallback/request state when `MoveToTargetSink` stops.
- Synchronize `Brain.PATH` with the path actually installed in navigation after move/recalculation instead of leaving stale path memory behind.
- Clear `Brain.PATH` and navigation cleanly when the active walk target disappears.

## Existing async path hardening retained
- Nonblocking POI/HOME/nearest-bed result handling and stale POI revalidation.
- Generator-scoped lock-free NodeEvaluator pools.
- Amphibious WALKABLE/WATER_BORDER path-cost isolation.
- Bounded async path queues and 4-vCPU steady/burst worker protection.
- Shared CPU budgeting and tracker allocation reductions.

## Upstream base
- Leaf 26.3 pinned at `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.
- No newer `ver/26.3` commit was available when this release was prepared.

## Validation / publishing
- Build the runnable Paperclip server JAR from the pinned upstream source.
- Start and stop real servers with both `balanced` and `extreme` profiles.
- Verify Warext runtime branding/config generation.
- Publish `Warext-Server-Engine-26.3.jar`, `SHA256SUMS.txt`, and `UPSTREAM_COMMIT.txt` to GitHub Releases only after successful validation.
- Fail the workflow if the versioned release does not contain the runnable `.jar` asset.

This release remains a prerelease while the 26.3 upstream line is still being stabilized.
