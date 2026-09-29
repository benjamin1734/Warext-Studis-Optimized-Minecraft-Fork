# Warext Studis Optimized Minecraft Fork

Performance-focused Minecraft server fork build project based on the official open-source Leaf project.

## Current target
- Upstream: `Winds-Studio/Leaf`
- Base branch: `dev/26.3`
- Java: 25
- Release line: `26.3-exp.1`
- Goal: minimize MSPT, p95/p99 tick latency, CPU time per tick, allocation/GC pressure and RAM use while keeping broad Paper/Leaf compatibility.

## Performance profiles

### Balanced — default
Recommended general-purpose profile.

```bash
java -Dwarext.profile=balanced -Xms4G -Xmx4G -jar Warext-Studis-Optimized-Leaf-26.3.jar nogui
```

Balanced enables optimized async pathfinding automatically on suitable multi-core CPUs, keeps CPU headroom for the main tick thread/GC/networking and leaves the experimental async entity tracker disabled by default.

### Extreme
For high-entity servers where maximum tick throughput matters more than conservative compatibility.

```bash
java -Dwarext.profile=extreme -Xms4G -Xmx4G -jar Warext-Studis-Optimized-Leaf-26.3.jar nogui
```

Extreme enables the optimized multi-threaded entity tracker and gives a larger share of the shared async CPU budget to pathfinding/tracking.

### Compatibility

```bash
java -Dwarext.profile=compatibility -Xms4G -Xmx4G -jar Warext-Studis-Optimized-Leaf-26.3.jar nogui
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
- Runtime Java 25 startup/shutdown smoke tests.

## Validation

Every release build must:
1. Apply the current Leaf/Paper patch stack.
2. Apply the Warext optimization layer.
3. Build the Paperclip JAR.
4. Start a real server with Java 25.
5. Reach the server `Done` state.
6. Shut down cleanly.
7. Pass both `balanced` and `extreme` profile smoke tests before publication.

Compilation and smoke tests prove build/runtime integrity; they do **not** prove a universal performance win on every workload. Real performance comparison should use p50/p95/p99 MSPT, CPU time/tick, allocation rate, GC pauses and heap use under the same workload.

## Licensing

This project is an independent derivative/build project and is not affiliated with the Leaf project. It inherits applicable licenses from Leaf, Paper and individual upstream patches. See `LICENSE.md`.
