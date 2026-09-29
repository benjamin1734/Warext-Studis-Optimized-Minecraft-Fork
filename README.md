# Warext Studis Optimized Minecraft Fork

Performance-focused Minecraft server fork build repository based on the official open-source Leaf project.

## Current base
- Upstream: Winds-Studio/Leaf
- Target branch: dev/26.3
- Java: 25
- Goal: minimize MSPT, p95/p99 tick latency, CPU time per tick, allocation/GC pressure and RAM usage while preserving broad Paper/Leaf plugin compatibility.

## Development principles
- Prefer measured work elimination over blindly adding threads.
- Use multi-core execution only where it lowers total tick latency.
- Reserve CPU headroom for the main tick thread, GC and networking.
- Avoid unbounded queues and main-thread queue flushing.
- Fix correctness/data-integrity issues before enabling experimental optimizations.
- Benchmark entity, tracker, pathfinding, chunk, block-entity, network and save paths separately.

This repository is an independent derivative/build project and is not affiliated with the Leaf project.
