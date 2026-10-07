# Warext Server Engine 26.3 exp.15

Experimental high-performance server engine build for the 26.3 line.

## Pregeneration chunk I/O pressure control
- Add a bounded pending-write guard for chunk NBT writes.
- Enabled by default only in the Warext `pregeneration` / `pregen` / `chunky` / `worldgen` profile.
- Keep Balanced, Extreme, and Compatibility behavior unchanged unless the option is explicitly enabled.
- Use heap-aware defaults:
  - <4 GiB max heap: soft 2048 pending writes.
  - 4-8 GiB: soft 4096 pending writes.
  - 8-16 GiB: soft 8192 pending writes.
  - 16+ GiB: soft 16384 pending writes.
- Default hard limit is 2x the soft limit.
- Soft pressure adds only a small extra drain.
- Hard pressure drains more aggressively, but each scheduler turn is capped to avoid creating a large synchronous I/O spike.
- Default extra drain burst is capped at 4 writes per IOWorker turn.
- Configuration:
  - `performance.chunk-io-pending-write-limit.enabled`
  - `soft-limit`
  - `hard-limit`
  - `max-extra-drains-per-turn`

This is based on the C2ME/DivineMC pending-write cache limiting idea reviewed in Leaf PR #946, but avoids an unbounded hard flush and uses Warext-specific bounded pressure draining.

## Chunky / 4-vCPU tuning retained
- Dedicated pregeneration profile.
- 4-vCPU worker policy:
  - Balanced: 2 chunk workers.
  - Extreme: 3 chunk workers.
  - Pregeneration: 3 chunk workers.
- Pregeneration keeps default AI/path/tracker async work out of the way so CPU headroom is available to chunk generation.
- Manual Paper worker-thread settings and the Warext chunk-worker system-property override remain authoritative.

## Validation
- Balanced real-server smoke boot.
- Extreme real-server smoke boot.
- Pregeneration real-server smoke boot.
- Pregeneration config must generate the chunk I/O pending-write guard as enabled.
- Deferred-container feature-enabled chest NBT restart/decode smoke remains mandatory.
- Runnable Paperclip JAR build and GitHub Release JAR verification remain mandatory.

## Upstream base
- Leaf 26.3 pinned at `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.

This release remains a prerelease while the Leaf 26.3 line is still being stabilized.
