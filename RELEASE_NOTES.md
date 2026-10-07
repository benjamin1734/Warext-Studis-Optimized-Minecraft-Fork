# Warext Server Engine 26.3 exp.14

Experimental high-performance server engine build for the 26.3 line.

## Chunky / world generation worker tuning
- Add a dedicated `pregeneration` profile; aliases: `pregen`, `chunky`, and `worldgen`.
- Replace Moonrise's automatic chunk-worker default with a Warext profile-aware policy when no explicit Paper worker count is configured.
- On a detected 4-core/vCPU host:
  - `balanced`: 2 chunk workers.
  - `extreme`: 3 chunk workers.
  - `pregeneration`: 3 chunk workers.
  - `compatibility`: preserves the conservative Moonrise-style result.
- The pregeneration profile disables default async pathfinding/tracker and AI activation optimizers that do not help offline Chunky generation, leaving more CPU headroom for chunk generation.
- Larger hosts scale worker count conservatively according to profile and keep CPU reserve for the tick thread, GC, Netty, and I/O.
- A manual Paper `worker-threads` value remains authoritative.
- The existing brand-specific `WorkerThreadCount` system property remains authoritative.
- Add an optional `-Dwarext.cpu.chunk-workers=N` override, clamped to 1-16 workers.
- Startup now logs the selected Warext profile, detected core count, chunk worker count, and I/O worker count.

The policy was developed after auditing Leaf PR #946 / C2ME-style worker-allocation ideas, but is implemented as a Warext-specific profile policy rather than wholesale-porting the closed PR.

## Validation
- Balanced real-server smoke boot.
- Extreme real-server smoke boot.
- Pregeneration/Chunky real-server smoke boot.
- Each profile must initialize the Warext-aware Moonrise worker policy.
- Deferred-container feature-enabled chest NBT restart/decode smoke from exp.13 remains enabled.
- Runnable Paperclip JAR build and GitHub Release asset verification remain mandatory.

## Existing worldgen/chunk optimizations retained
- Leaf 26.3 worldgen outward-iteration cache bypass (#935).
- Chunk-cache p99-safe removal paths.
- Chunk packet block-entity metadata pre-sizing.
- Deferred chest/barrel/shulker item decoding, opt-in.
- Precipitation current-chunk / heightmap / biome lookup reuse.
- Adaptive async save/compression workers and NBT allocation reductions.

## Upstream base
- Leaf 26.3 pinned at `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.

## Publishing
- Publish `Warext-Server-Engine-26.3.jar`, `SHA256SUMS.txt`, and `UPSTREAM_COMMIT.txt` only after all smoke tests pass.
- Fail the workflow if the release is missing the runnable `.jar`.

This release remains a prerelease while the Leaf 26.3 line is still being stabilized.
