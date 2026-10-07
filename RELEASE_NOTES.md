# Warext Server Engine 26.3 exp.16

Experimental high-performance server engine build for the 26.3 line.

## Sampler-safe The End biome cache
- Add a per-worldgen-thread cache for The End biome lookups.
- Enabled by default only in the Warext `pregeneration` / `pregen` / `chunky` / `worldgen` profile.
- Default cache capacity: 2048 entries per worldgen thread.
- Configuration:
  - `performance.worldgen.end-biome-cache.enabled`
  - `performance.worldgen.end-biome-cache.cache-capacity`
- Cache capacity is clamped to 128-16384 entries.
- Uses an access-ordered FastUtil primitive-long map to avoid object-key allocation in the hot path.

## Correctness improvements over the reviewed C2ME/Leaf variant
- Cache identity includes quart X, quart Y, and quart Z; vertical biome queries are not collapsed onto the same entry.
- Cache is partitioned by `Climate.Sampler`: when the same biome source is queried with a different sampler/world seed, the thread-local cache is cleared before reuse.
- The original vanilla biome resolver remains available as a direct fallback whenever the optimization is disabled.
- The central End-island fast path and vanilla erosion thresholds are unchanged.

The optimization is inspired by C2ME's End biome cache as reviewed in Leaf PR #946, but Warext intentionally adds vertical-coordinate and sampler isolation for world-generation correctness.

## Pregeneration stack retained
- 4-vCPU Warext chunk worker policy:
  - Balanced: 2 workers.
  - Extreme: 3 workers.
  - Pregeneration: 3 workers.
- Pregeneration AI/path/tracker defaults remain disabled so CPU headroom stays available for world generation.
- Bounded chunk NBT pending-write pressure control remains enabled by default in pregeneration mode.
- Deferred container item decoding remains available as an opt-in feature.

## Validation
- Balanced real-server smoke boot.
- Extreme real-server smoke boot.
- Pregeneration real-server smoke boot.
- Pregeneration config must enable:
  - chunk I/O pending-write guard;
  - End biome cache.
- Pregeneration smoke force-loads a real End chunk outside the central-island fast path to exercise erosion-based End biome generation with the cache enabled.
- Deferred-container chest NBT restart/decode smoke remains mandatory.
- Runnable Paperclip JAR build and GitHub Release asset verification remain mandatory.

## Upstream base
- Leaf 26.3 pinned at `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.

This release remains a prerelease while the Leaf 26.3 line is still being stabilized.
