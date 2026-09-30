# Warext Server Engine 26.3 exp.2

Experimental high-performance server engine build for the 26.3 line.

## Main changes
- Shared adaptive CPU budget for pathfinding, entity tracking and async save/compression workers.
- Multi-core async pathfinding with safer queue policy, evaluator isolation and reduced synchronization.
- Async entity-tracker allocation reductions, reusable contexts, reusable packet buffers and primitive interpolation accumulation.
- Async player/world save coalescing with compression moved off the tick thread and safer write ordering.
- Async mob-spawn recovery so worker failures do not permanently stop natural spawning.
- Entity activation, despawn and natural-spawn spatial lookup allocation reductions.
- Adaptive linear nearest-player fast path for small player counts.
- Chunk cache p99 protection by avoiding hot-path shrink rehashes.
- Collision step buffer reuse.
- Precipitation/random-tick lookup reuse.
- Async path lifecycle cleanup, bounded path queues and evaluator-pool retention limits.
- Entity encode-id caching and reusable tracker Future/join wrapper arrays.
- Light packet ThreadLocal buffer reference cleanup to reduce retained heap.
- Real Java 25 startup/shutdown smoke tests in CI for both balanced and extreme profiles.
- Product-facing branding standardized as Warext Server Engine.

## Profiles
- `compatibility`: conservative async defaults.
- `balanced`: default; optimized async pathfinding on suitable CPUs, async tracker off by default.
- `extreme`: enables the optimized async tracker, experimental entity-activation/random-tick/sleeping-block-entity/DAB optimizers, and allocates more async CPU headroom.

Select with `-Dwarext.profile=balanced` or `-Dwarext.profile=extreme`.

This release remains a prerelease because the 26.3 upstream base is still under active development.
