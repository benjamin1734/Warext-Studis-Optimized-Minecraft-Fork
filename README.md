# Warext Server Engine

High-performance Minecraft server engine focused on low MSPT, low p95/p99 tick latency, efficient CPU scheduling, reduced allocation/GC pressure and low memory overhead.

## Current target
- Minecraft line: 26.3
- Java: 25
- Release line: `26.3-exp.3`
- Goal: minimize MSPT, p95/p99 tick latency, CPU time per tick, allocation/GC pressure and RAM use while preserving broad Paper ecosystem compatibility.

## Performance profiles

### Balanced — default
Recommended general-purpose profile.

```bash
java -Dwarext.profile=balanced -Xms4G -Xmx4G -jar Warext-Server-Engine-26.3.jar nogui
```

Balanced enables optimized async pathfinding automatically on suitable multi-core CPUs, enables Dynamic Activation of Brain with its compatibility blacklist, keeps extra CPU headroom on 4-vCPU hosts for the main tick thread/GC/networking, and leaves the experimental async entity tracker disabled by default.

### Extreme
For high-entity servers where maximum tick throughput matters more than conservative compatibility.

```bash
java -Dwarext.profile=extreme -Xms4G -Xmx4G -jar Warext-Server-Engine-26.3.jar nogui
```

Extreme enables the optimized multi-threaded entity tracker, experimental entity-activation/random-tick/sleeping-block-entity/DAB optimizers, and gives a larger share of the shared async CPU budget to pathfinding/tracking.

### Compatibility

```bash
java -Dwarext.profile=compatibility -Xms4G -Xmx4G -jar Warext-Server-Engine-26.3.jar nogui
```

Use this when plugin compatibility matters more than experimental async performance.

## CPU overrides

Automatic CPU budgeting is recommended. Manual overrides are available when benchmarking a specific host:

- `-Dwarext.cpu.reserve=N`
- `-Dwarext.cpu.pathfinding-threads=N`
- `-Dwarext.cpu.tracker-threads=N`
- `-Dwarext.cpu.io-workers=N`
- `-Dwarext.kdtree.linear-threshold=N`

The automatic scheduler reserves CPU headroom instead of letting every subsystem independently consume all logical processors.

## Main optimization areas

- Adaptive multi-core pathfinding.
- Shared CPU budgeting across async systems.
- NodeEvaluator pool correctness and reduced contention.
- Async entity-tracker context, snapshot, packet-buffer and interpolation allocation reductions.
- Async save coalescing and off-thread NBT compression.
- Async mob-spawn failure recovery.
- Entity activation/despawn/natural-spawn spatial lookup allocation reductions.
- Adaptive small-player linear nearest-player fast path.
- ChunkCache p99 rehash-spike avoidance.
- Collision buffer reuse.
- Random-tick/precipitation lookup reuse.
- Async path lifecycle cleanup and bounded path queues.
- Entity encode-id caching.
- Light packet ThreadLocal retention cleanup.
- VoxelBench-driven 4-vCPU scheduler tuning, balanced DAB defaults, Alternate Current defaults for new configs, and chunk-packet metadata allocation reductions.
- Reusable packet-NBT DataOutput adapter to reduce per-object serialization allocation and GC pressure.
- Runtime Java 25 startup/shutdown smoke tests for both balanced and extreme profiles.
- Reproducible builds pinned to a tested upstream commit.

## Validation

Every release build must:
1. Fetch the pinned upstream source revision.
2. Apply the upstream patch stack.
3. Apply the Warext performance layer.
4. Build the server JAR.
5. Start a real server with Java 25.
6. Reach the server `Done` state.
7. Shut down cleanly.
8. Pass both `balanced` and `extreme` profile smoke tests before publication.

Compilation and smoke tests prove build/runtime integrity; they do **not** prove a universal performance win on every workload. Real performance comparison should use p50/p95/p99 MSPT, CPU time/tick, allocation rate, GC pauses and heap use under the same workload.

## Licensing and attribution

Warext Server Engine uses and modifies open-source upstream components. Required upstream attribution and license details are kept in `LICENSE.md`.
