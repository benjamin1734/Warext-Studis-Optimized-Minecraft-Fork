# Warext Optimized Leaf 26.3 exp.1

Experimental performance-focused build based on Leaf dev/26.3.

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

## Profiles
- `compatibility`: conservative async defaults.
- `balanced`: default; optimized async pathfinding on suitable CPUs, async tracker off by default.
- `extreme`: enables the optimized async tracker, experimental entity-activation/random-tick/sleeping-block-entity/DAB optimizers, and allocates more async CPU headroom.

Select with `-Dwarext.profile=balanced` or `-Dwarext.profile=extreme`.

Leaf 26.3 itself is still a development branch, so this release is marked prerelease.
