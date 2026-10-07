# Warext Server Engine 26.3 exp.17

Experimental high-performance server engine build for the 26.3 line.

## Vanilla-equivalent Aquifer center precompute
- Add `performance.worldgen.aquifer-center-precompute.enabled`.
- Enabled by default only in the Warext `pregeneration` / `pregen` / `chunky` / `worldgen` profile.
- Precompute Aquifer sample-center locations once per `NoiseBasedAquifer` instance instead of lazily deriving the same positional RNG samples inside the per-block substance hot path.
- Use vanilla `PositionalRandomFactory.at(x, y, z)` and the same `nextInt(10)`, `nextInt(9)`, `nextInt(10)` sequence for every grid cell.
- Leave vanilla closest-four selection, distance ordering, pressure calculation, barrier noise, fluid-update scheduling and block-state decisions unchanged.
- Do not expose or mutate random-generator internals and do not port the C2ME branchless nearest-center rewrite in this release.
- Replace `Math.floorDiv(x, 64)` / `Math.floorDiv(z, 64)` with arithmetic shifts for the power-of-two fluid-type cells; this is mathematically equivalent for signed integer coordinates.

## Why this is safer than the full C2ME Aquifer patch
Leaf PR #946's Aquifer patch also rewrites positional RNG internals, pre-packs aquifer positions, replaces nearest-four selection and rewrites pressure math. Warext exp.17 deliberately takes only the center-precompute idea while preserving vanilla decision logic, so world-generation behavior stays tied to the same vanilla RNG calls and ordering.

## Validation
- Balanced real-server smoke boot.
- Extreme real-server smoke boot.
- Pregeneration real-server smoke boot.
- Pregeneration chunk-I/O guard and End-biome cache config validation.
- Real distant End chunk generation smoke.
- Dedicated normal-noise-world Aquifer smoke with fixed seed `8675309`.
- Aquifer smoke must enable `aquifer-center-precompute`, force-load Overworld chunk `[32,32]`, save successfully and stop cleanly without Aquifer/worldgen errors.
- Deferred-container chest NBT restart/decode smoke remains mandatory.
- Runnable Paperclip JAR build and GitHub Release JAR verification remain mandatory.

## Existing pregeneration stack retained
- 4-vCPU worker policy: Balanced 2, Extreme 3, Pregeneration 3.
- Pregeneration disables default AI/path/tracker background work that does not help offline world generation.
- Bounded chunk NBT pending-write pressure control.
- Sampler-safe, XYZ-aware The End biome cache.
- Deferred container item decoding remains opt-in.

## Upstream base
- Leaf 26.3 remains pinned at `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`; no newer `ver/26.3` commit was available when this release was prepared.

This release remains a prerelease while the Leaf 26.3 line is still being stabilized.
