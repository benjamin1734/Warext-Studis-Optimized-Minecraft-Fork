#!/usr/bin/env python3
from pathlib import Path
import sys

root = Path(sys.argv[1]).resolve()

def read(rel):
    return (root / rel).read_text(encoding="utf-8")

def write(rel, data):
    (root / rel).write_text(data, encoding="utf-8")

def replace_once(rel, old, new, label):
    data = read(rel)
    count = data.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match in {rel}, got {count}")
    write(rel, data.replace(old, new, 1))
    print(f"[ok] {label}")

def insert_after_once(rel, needle, addition, label):
    data = read(rel)
    count = data.count(needle)
    if count != 1:
        raise RuntimeError(f"{label}: expected 1 match in {rel}, got {count}")
    write(rel, data.replace(needle, needle + addition, 1))
    print(f"[ok] {label}")

# Pathfinding: actually use configured multi-core worker count before queueing.
replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/async/path/AsyncPathProcessor.java",
    """    private static int getCorePoolSize() {
        return 1;
    }""",
    """    private static int getCorePoolSize() {
        return getMaxPoolSize();
    }""",
    "pathfinding core pool scaling",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/async/path/AsyncPathProcessor.java",
    """            PATH_PROCESSING_EXECUTOR = new ThreadPoolExecutor(
                getCorePoolSize(),
                getMaxPoolSize(),
                getKeepAliveTime(), TimeUnit.SECONDS,
                getQueueImpl(),
                getThreadFactory(),
                getRejectedPolicy()
            );""",
    """            PATH_PROCESSING_EXECUTOR = new ThreadPoolExecutor(
                getCorePoolSize(),
                getMaxPoolSize(),
                getKeepAliveTime(), TimeUnit.SECONDS,
                getQueueImpl(),
                getThreadFactory(),
                getRejectedPolicy()
            );
            if (getKeepAliveTime() > 0L) {
                PATH_PROCESSING_EXECUTOR.allowCoreThreadTimeOut(true);
            }""",
    "pathfinding idle worker timeout",
)

# Reserve CPU headroom for main tick, GC and networking instead of oversubscribing.
replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/async/AsyncPathfinding.java",
    "public static PathfindTaskRejectPolicy asyncPathfindingRejectPolicy = PathfindTaskRejectPolicy.FLUSH_ALL;",
    "public static PathfindTaskRejectPolicy asyncPathfindingRejectPolicy = PathfindTaskRejectPolicy.CALLER_RUNS;",
    "pathfinding safer p99 reject default",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/async/AsyncPathfinding.java",
    """        if (asyncPathfindingMaxThreads <= 0) {
            asyncPathfindingMaxThreads = Math.max(availableProcessors / 4, 1);
        }""",
    """        if (asyncPathfindingMaxThreads <= 0) {
            final int reservedCores = availableProcessors >= 12 ? 3 : (availableProcessors >= 6 ? 2 : 1);
            final int workerBudget = Math.max(1, availableProcessors - reservedCores);
            asyncPathfindingMaxThreads = Math.max(1, Math.min(6, workerBudget / 3));
        }""",
    "adaptive pathfinding cpu budget",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/async/AsyncPathfinding.java",
    """        asyncPathfindingRejectPolicy = PathfindTaskRejectPolicy.fromString(globalConfig.getString(basePath() + ".reject-policy",
            availableProcessors >= 12 && asyncPathfindingQueueSize < 512
                ? PathfindTaskRejectPolicy.FLUSH_ALL.toString()
                : PathfindTaskRejectPolicy.CALLER_RUNS.toString())
        );""",
    """        asyncPathfindingRejectPolicy = PathfindTaskRejectPolicy.fromString(globalConfig.getString(
            basePath() + ".reject-policy",
            PathfindTaskRejectPolicy.CALLER_RUNS.toString()
        ));""",
    "avoid flush-all main-thread burst by default",
)

# Fix evaluator-pool cross-contamination while retaining pooling.
node_cache = """package org.dreeam.leaf.async.path;

import it.unimi.dsi.fastutil.ints.Int2ObjectOpenHashMap;
import it.unimi.dsi.fastutil.objects.Reference2ObjectOpenHashMap;
import net.minecraft.world.level.pathfinder.BinaryHeap;
import net.minecraft.world.level.pathfinder.Node;
import net.minecraft.world.level.pathfinder.NodeEvaluator;
import org.apache.commons.lang3.Validate;

import java.util.ArrayDeque;

public final class NodeEvaluatorCache {

    private static final Reference2ObjectOpenHashMap<NodeEvaluatorGenerator, Int2ObjectOpenHashMap<ArrayDeque<NodeEvaluator>>> NODE_EVALUATORS = new Reference2ObjectOpenHashMap<>();
    private static final Reference2ObjectOpenHashMap<NodeEvaluator, ArrayDeque<NodeEvaluator>> NODE_EVALUATOR_TO_POOL = new Reference2ObjectOpenHashMap<>();

    public static final ThreadLocal<BinaryHeap> HEAP_LOCAL = ThreadLocal.withInitial(BinaryHeap::new);
    public static final ThreadLocal<Node[]> NEIGHBORS_LOCAL = ThreadLocal.withInitial(() -> new Node[32]);

    private NodeEvaluatorCache() {
    }

    public static synchronized NodeEvaluator takeNodeEvaluator(NodeEvaluatorGenerator generator, NodeEvaluator localNodeEvaluator) {
        final int nodeEvaluatorFeatures = NodeEvaluatorFeatures.fromNodeEvaluator(localNodeEvaluator);
        final Int2ObjectOpenHashMap<ArrayDeque<NodeEvaluator>> generatorEvaluators =
            NODE_EVALUATORS.computeIfAbsent(generator, key -> new Int2ObjectOpenHashMap<>());
        final ArrayDeque<NodeEvaluator> pool =
            generatorEvaluators.computeIfAbsent(nodeEvaluatorFeatures, key -> new ArrayDeque<>());

        NodeEvaluator nodeEvaluator = pool.poll();
        if (nodeEvaluator == null) {
            nodeEvaluator = generator.generate(NodeEvaluatorFeatures.unpack(nodeEvaluatorFeatures));
        }

        NODE_EVALUATOR_TO_POOL.put(nodeEvaluator, pool);
        return nodeEvaluator;
    }

    public static synchronized void returnNodeEvaluator(final NodeEvaluator nodeEvaluator) {
        final ArrayDeque<NodeEvaluator> pool = NODE_EVALUATOR_TO_POOL.remove(nodeEvaluator);
        Validate.notNull(pool, "NodeEvaluator already returned");
        pool.offer(nodeEvaluator);
    }

    public static synchronized void removeNodeEvaluator(final NodeEvaluator nodeEvaluator) {
        NODE_EVALUATOR_TO_POOL.remove(nodeEvaluator);
    }
}
"""
write("leaf-server/src/main/java/org/dreeam/leaf/async/path/NodeEvaluatorCache.java", node_cache)
print("[ok] generator-scoped NodeEvaluator pools")

# Tracker: adaptive multi-core worker budget.
replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/async/MultithreadedTracker.java",
    """        if (threads <= 0) {
            threads = Math.min(Runtime.getRuntime().availableProcessors() / 2, 4);
        }""",
    """        if (threads <= 0) {
            final int availableProcessors = Runtime.getRuntime().availableProcessors();
            final int reservedCores = availableProcessors >= 12 ? 3 : (availableProcessors >= 6 ? 2 : 1);
            final int workerBudget = Math.max(1, availableProcessors - reservedCores);
            threads = Math.max(1, Math.min(6, workerBudget / 2));
        }""",
    "adaptive tracker cpu budget",
)

# Tracker: double-buffer capture map and reuse growing entity snapshot.
replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/async/tracker/AsyncTracker.java",
    """    private final Reference2ReferenceOpenHashMap<ChunkMap.TrackedEntity, TrackerInput> trackers = new Reference2ReferenceOpenHashMap<>();
    private final Reference2ReferenceOpenHashMap<ChunkMap.TrackedEntity, TrackerInput> capture = new Reference2ReferenceOpenHashMap<>();""",
    """    private final Reference2ReferenceOpenHashMap<ChunkMap.TrackedEntity, TrackerInput> trackers = new Reference2ReferenceOpenHashMap<>();
    private Reference2ReferenceOpenHashMap<ChunkMap.TrackedEntity, TrackerInput> capture = new Reference2ReferenceOpenHashMap<>();
    private Reference2ReferenceOpenHashMap<ChunkMap.TrackedEntity, TrackerInput> captureSpare = new Reference2ReferenceOpenHashMap<>();
    private ChunkMap.TrackedEntity[] trackerSnapshot = new ChunkMap.TrackedEntity[0];""",
    "tracker reusable buffers",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/async/tracker/AsyncTracker.java",
    """    public void tick(final ServerLevel world) {
        Reference2ReferenceOpenHashMap<ChunkMap.TrackedEntity, TrackerInput> cap = capture.clone();
        capture.clear();
        handlePlayer(world);""",
    """    public void tick(final ServerLevel world) {
        Reference2ReferenceOpenHashMap<ChunkMap.TrackedEntity, TrackerInput> cap = this.capture;
        this.capture = this.captureSpare;
        this.captureSpare = cap;
        this.capture.clear();
        handlePlayer(world);""",
    "tracker capture double buffer",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/async/tracker/AsyncTracker.java",
    """        ChunkMap.TrackedEntity[] raw = new ChunkMap.TrackedEntity[len];
        System.arraycopy(trackersRef.getRawDataUnchecked(), 0, raw, 0, len);
        TrackerSlice slice = new TrackerSlice(raw);""",
    """        if (this.trackerSnapshot.length < len) {
            int newCapacity = Math.max(64, this.trackerSnapshot.length);
            while (newCapacity < len) {
                newCapacity <<= 1;
            }
            this.trackerSnapshot = new ChunkMap.TrackedEntity[newCapacity];
        }
        ChunkMap.TrackedEntity[] raw = this.trackerSnapshot;
        System.arraycopy(trackersRef.getRawDataUnchecked(), 0, raw, 0, len);
        TrackerSlice slice = new TrackerSlice(raw, 0, len);""",
    "tracker snapshot reuse",
)

# Async mob counting: a worker exception must never permanently stall natural spawning.
server_chunk_cache = "leaf-server/src/minecraft/java/net/minecraft/server/level/ServerChunkCache.java"
replace_once(
    server_chunk_cache,
    "    private boolean spawnCountPending; // Leaf - Pufferfish - async mob spawning",
    "    private volatile boolean spawnCountPending; // Leaf - Pufferfish - async mob spawning",
    "mob spawn pending visibility",
)
replace_once(
    server_chunk_cache,
    """            this.level.getServer().mobSpawnExecutor.submit(() -> {
                this.asyncSpawnState = NaturalSpawner.createStateAsync(spawnableChunkCount, entities, this.level, environmentAttributes, countAllMobsForSpawning);
            });""",
    """            this.level.getServer().mobSpawnExecutor.submit(() -> {
                try {
                    this.asyncSpawnState = NaturalSpawner.createStateAsync(spawnableChunkCount, entities, this.level, environmentAttributes, countAllMobsForSpawning);
                } catch (Throwable throwable) {
                    this.spawnCountPending = false;
                    LOGGER.error("Failed to asynchronously count mobs; the next tick will retry safely", throwable);
                }
            });""",
    "mob spawn exception recovery",
)

# Async save: never replace valid data after compression failure.
insert_after_once(
    "leaf-server/src/minecraft/java/net/minecraft/world/level/storage/LevelStorageSource.java",
    '                LevelStorageSource.LOGGER.error("Failed to encode level {}", worldDir, e);',
    '\n                return;',
    "level.dat compression failure safety",
)
insert_after_once(
    "leaf-server/src/minecraft/java/net/minecraft/world/level/storage/PlayerDataStorage.java",
    '            LOGGER.warn("Failed to encode player data for {}", stringId, exception);',
    '\n            return;',
    "player.dat compression failure safety",
)

print("All Warext optimized Leaf 26.3 performance patches applied.")
