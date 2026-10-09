# Warext Server Engine 26.3 exp.20

Experimental high-performance Minecraft server engine for the 26.3 line.

## Hardware-neutral defaults
- Remove the earlier assumption that Warext must be tuned for one specific VPS CPU or RAM size.
- Replace the hard-coded small-host chunk-worker tiers with a conservative automatic policy based on processors visible to the JVM, Moonrise physical-core detection and the selected performance profile.
- Respect CPU quotas/container limits via `Runtime.getRuntime().availableProcessors()` when selecting automatic chunk-worker counts.
- For balanced/extreme/pregeneration, reserve main-thread headroom and scale chunk workers as a fraction of available processing capacity, not as special-cased host specifications.
- Replace the previous four-CPU-specific pathfinding steady/burst branch with an adaptive steady worker budget derived from the shared async budget.
- Existing explicit Paper chunk worker values, brand-specific WorkerThreadCount and `-Dwarext.cpu.chunk-workers=N` overrides remain authoritative. Other CPU override properties are unchanged.
- Remove fixed Java heap recommendations from README launch commands; users should size memory for their actual hardware, plugins and workload.
- No fixed or assumed server CPU/RAM specification is an official Warext target.

## Moonrise direct ticking cache correctness
- Retain the experimental opt-in `performance.chunk.direct-ticking-set-lookups.enabled: false` default from exp.19.
- Add optional runtime parity verification with `-Dwarext.debug.validate-ticking-sets=true`.
- Parity verification compares each optimized block/entity ticking result against the current Moonrise chunk-holder readiness and raises an error on disagreement.
- Extend the feature-enabled CI smoke to load and release positive and negative chunk coordinates, repeat ticket transitions, save and cleanly stop.

## CI coverage
- Apply pinned upstream Leaf 26.3 patches and Warext performance layer.
- Compile a runnable Paperclip JAR.
- Start/stop real balanced, extreme, pregeneration, compatibility and experimental direct-ticking configurations.
- Additional CPU-envelope smoke checks use simulated JVM ActiveProcessorCount values of 2 and 8 **as test cases only**, not recommended host hardware.
- Keep existing distant End biome, normal Aquifer worldgen, chunk save/reload, and lazy-container NBT smoke tests.
- Publish the runnable JAR, SHA256SUMS and upstream ref only if every CI stage succeeds.

## Upstream
- Leaf `ver/26.3` remains pinned at `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`; no newer upstream 26.3 commit was available during this update.

This is a prerelease. Startup smoke and parity probes are not replacements for a controlled, long-running throughput/MSPT/GC benchmark on real workloads.
