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


# 8) Chunk lookup: avoid full-table shrink rehashes on normal unload ticks.
replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/world/ChunkCache.java",
    """        if (n > MIN_N && size < maxFill / 4 && n > it.unimi.dsi.fastutil.Hash.DEFAULT_INITIAL_SIZE) {
            rehash(n / 2);
        }
        return oldValue;""",
    """        // Warext: do not shrink during ordinary chunk removal. A full rehash here can
        // create a large p99 MSPT spike after exploration/mass chunk unload.
        return oldValue;""",
    "chunk cache p99-safe removal",
)
replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/world/ChunkCache.java",
    """        size--;
        if (n > MIN_N && size < maxFill / 4 && n > it.unimi.dsi.fastutil.Hash.DEFAULT_INITIAL_SIZE) rehash(n / 2);
        return oldValue;""",
    """        size--;
        // Warext: keep capacity on hot-path removals; clear() still releases references.
        return oldValue;""",
    "chunk cache p99-safe null-key removal",
)

# 9) KD trees: allow reusable over-capacity buffers while only indexing active players.
for rel, dims in [
    ("leaf-server/src/main/java/org/dreeam/leaf/util/KDTree2D.java", 2),
    ("leaf-server/src/main/java/org/dreeam/leaf/util/KDTree3D.java", 3),
]:
    data = read(rel)
    old_sig = "    public void build(final double[][] coords, final int[] indices) {"
    if data.count(old_sig) != 1:
        raise RuntimeError(f"KD build overload: expected one signature in {rel}")
    data = data.replace(
        old_sig,
        """    public void build(final double[][] coords, final int[] indices) {
        build(coords, indices, indices.length);
    }

    public void build(final double[][] coords, final int[] indices, final int length) {""",
        1,
    )
    data = data.replace(
        f"        if (indices.length == 0 || coords.length != {dims}) {{",
        f"        if (length == 0 || coords.length != {dims}) {{",
        1,
    )
    data = data.replace(
        "        for (int i = 0; i < indices.length; i++) {",
        "        if (length < 0 || length > indices.length) throw new IllegalArgumentException(\"Invalid KD-tree length\");\n        for (int i = 0; i < length; i++) {",
        1,
    )
    data = data.replace(
        "        stack[st++] = new Node(SENTINEL, false, 0, indices.length, 0);",
        "        stack[st++] = new Node(SENTINEL, false, 0, length, 0);",
        1,
    )
    data = data.replace(
        "        ensureSearch(indices.length, nodeLen);",
        "        ensureSearch(length, nodeLen);",
        1,
    )
    write(rel, data)
    print(f"[ok] KDTree{dims}D active-length build")

# 10) Entity activation: reuse player/range/index buffers instead of allocating them every world tick.
entity_activation = "leaf-server/src/main/java/org/dreeam/leaf/world/EntityActivation.java"
replace_once(
    entity_activation,
    """    private final KDTree2D kdTree2 = new KDTree2D();
    private final KDTree3D kdTree3 = new KDTree3D();""",
    """    private final KDTree2D kdTree2 = new KDTree2D();
    private final KDTree3D kdTree3 = new KDTree3D();
    private ServerPlayer[] playerBuffer = EMPTY_PLAYERS;
    private double[] playerX = new double[0];
    private double[] playerY = new double[0];
    private double[] playerZ = new double[0];
    private int[] playerIndices = new int[0];
    private final double[][] kd2Coords = new double[2][];
    private final double[][] kd3Coords = new double[3][];
    private final double[] activationRanges = new double[ACTIVATION_TYPES.length];
    private int lastPlayerSize;
    private double lastActivationDistanceMod = Double.NaN;
    private double cachedActivationScale = 1.0;

    private void ensurePlayerCapacity(final int required) {
        if (this.playerBuffer.length >= required) {
            return;
        }
        int capacity = Math.max(8, this.playerBuffer.length);
        while (capacity < required) {
            capacity <<= 1;
        }
        this.playerBuffer = new ServerPlayer[capacity];
        this.playerX = new double[capacity];
        this.playerY = new double[capacity];
        this.playerZ = new double[capacity];
        this.playerIndices = new int[capacity];
        this.kd2Coords[0] = this.playerX;
        this.kd2Coords[1] = this.playerZ;
        this.kd3Coords[0] = this.playerX;
        this.kd3Coords[1] = this.playerY;
        this.kd3Coords[2] = this.playerZ;
    }""",
    "entity activation reusable buffers",
)

replace_once(
    entity_activation,
    """        final double[] ranges = new double[ACTIVATION_TYPES.length];
        ranges[ActivationType.WATER.ordinal()] = waterActivationRange;
        ranges[ActivationType.FLYING_MONSTER.ordinal()] = flyingActivationRange;
        ranges[ActivationType.VILLAGER.ordinal()] = villagerActivationRange;
        ranges[ActivationType.MONSTER.ordinal()] = monsterActivationRange;
        ranges[ActivationType.ANIMAL.ordinal()] = animalActivationRange;
        ranges[ActivationType.RAIDER.ordinal()] = raiderActivationRange;
        ranges[ActivationType.MISC.ordinal()] = miscActivationRange;
        for (int i = 0; i < ranges.length; i++) {
            if (ranges[i] > 0.0) {
                ranges[i] = ranges[i] * ranges[i];
            }
        }""",
    """        final double[] ranges = this.activationRanges;
        ranges[ActivationType.WATER.ordinal()] = waterActivationRange * (double) waterActivationRange;
        ranges[ActivationType.FLYING_MONSTER.ordinal()] = flyingActivationRange * (double) flyingActivationRange;
        ranges[ActivationType.VILLAGER.ordinal()] = villagerActivationRange * (double) villagerActivationRange;
        ranges[ActivationType.MONSTER.ordinal()] = monsterActivationRange * (double) monsterActivationRange;
        ranges[ActivationType.ANIMAL.ordinal()] = animalActivationRange * (double) animalActivationRange;
        ranges[ActivationType.RAIDER.ordinal()] = raiderActivationRange * (double) raiderActivationRange;
        ranges[ActivationType.MISC.ordinal()] = miscActivationRange * (double) miscActivationRange;""",
    "entity activation range allocation removal",
)

replace_once(
    entity_activation,
    """        int playerSize = 0;
        final ServerPlayer[] players = world.players().toArray(EMPTY_PLAYERS);
        final double[] pxl = new double[players.length];
        final double[] pyl = new double[players.length];
        final double[] pzl = new double[players.length];
        for (int i = 0; i < players.length; i++) {
            final ServerPlayer p = players[i];
            p.activatedTick = currentTick;
            if (world.spigotConfig.ignoreSpectatorActivation && p.isSpectator()) {
                continue;
            }
            if (!world.purpurConfig.idleTimeoutTickNearbyEntities && p.isAfk()) {
                continue; // Purpur - AFK API
            }
            players[playerSize] = p;
            pxl[playerSize] = p.getX();
            pyl[playerSize] = p.getY();
            pzl[playerSize] = p.getZ();
            playerSize++;
        }""",
    """        int playerSize = 0;
        final java.util.List<ServerPlayer> worldPlayers = world.players();
        ensurePlayerCapacity(worldPlayers.size());
        final ServerPlayer[] players = this.playerBuffer;
        final double[] pxl = this.playerX;
        final double[] pyl = this.playerY;
        final double[] pzl = this.playerZ;
        for (int i = 0, worldPlayerCount = worldPlayers.size(); i < worldPlayerCount; i++) {
            final ServerPlayer p = worldPlayers.get(i);
            p.activatedTick = currentTick;
            if (world.spigotConfig.ignoreSpectatorActivation && p.isSpectator()) {
                continue;
            }
            if (!world.purpurConfig.idleTimeoutTickNearbyEntities && p.isAfk()) {
                continue; // Purpur - AFK API
            }
            players[playerSize] = p;
            pxl[playerSize] = p.getX();
            pyl[playerSize] = p.getY();
            pzl[playerSize] = p.getZ();
            playerSize++;
        }
        if (playerSize < this.lastPlayerSize) {
            java.util.Arrays.fill(players, playerSize, this.lastPlayerSize, null);
        }
        this.lastPlayerSize = playerSize;""",
    "entity activation player snapshot reuse",
)

replace_once(
    entity_activation,
    """        final int[] indices = new int[playerSize];
        kdTree2.build(new double[][]{pxl, pzl}, indices);
        if (dab) kdTree3.build(new double[][]{pxl, pyl, pzl}, indices);""",
    """        final int[] indices = this.playerIndices;
        kdTree2.build(this.kd2Coords, indices, playerSize);
        if (dab) kdTree3.build(this.kd3Coords, indices, playerSize);""",
    "entity activation KD input reuse",
)

replace_once(
    entity_activation,
    """                DynamicActivationofBrain.startDistanceSquared,
                Math.pow(2.0, -DynamicActivationofBrain.activationDistanceMod),
                DynamicActivationofBrain.maximumActivationPrio""",
    """                DynamicActivationofBrain.startDistanceSquared,
                this.activationScale(),
                DynamicActivationofBrain.maximumActivationPrio""",
    "entity activation cached DAB scale",
)

replace_once(
    entity_activation,
    """    private static void activateEntities(int size, Object[] entities, boolean tickMarkers, long currentTick, double[] ranges, KDTree2D kdTree2, boolean dab, boolean dontEnableIfInWater, KDTree3D kdTree3, double startSq, double scale, int maxPriority) {""",
    """    private double activationScale() {
        final double mod = DynamicActivationofBrain.activationDistanceMod;
        if (Double.doubleToLongBits(mod) != Double.doubleToLongBits(this.lastActivationDistanceMod)) {
            this.lastActivationDistanceMod = mod;
            this.cachedActivationScale = Math.pow(2.0, -mod);
        }
        return this.cachedActivationScale;
    }

    private static void activateEntities(int size, Object[] entities, boolean tickMarkers, long currentTick, double[] ranges, KDTree2D kdTree2, boolean dab, boolean dontEnableIfInWater, KDTree3D kdTree3, double startSq, double scale, int maxPriority) {""",
    "entity activation DAB scale cache helper",
)

# 11) Move NBT compression off the tick thread; the worker writes directly to the temp file.
level_storage = "leaf-server/src/minecraft/java/net/minecraft/world/level/storage/LevelStorageSource.java"
replace_once(
    level_storage,
    """            // Leaf start - Async playerdata saving
            // Save level.dat asynchronously
            var nbtBytes = new it.unimi.dsi.fastutil.io.FastByteArrayOutputStream(65536);
            try {
                NbtIo.writeCompressed(root, nbtBytes);
            } catch (Exception e) {
                LevelStorageSource.LOGGER.error("Failed to encode level {}", worldDir, e);
                return;
            }
            org.dreeam.leaf.async.AsyncPlayerDataSaving.submit(() -> {
                try {
                    Path dataFile = Files.createTempFile(worldDir, "level", ".dat");
                    org.apache.commons.io.FileUtils.writeByteArrayToFile(dataFile.toFile(), nbtBytes.array, 0, nbtBytes.length, false);
                    Path oldDataFile = this.levelDirectory.oldDataFile();
                    Path currentFile = this.levelDirectory.dataFile();
                    Util.safeReplaceFile(currentFile, dataFile, oldDataFile);
                } catch (Exception e) {
                    LevelStorageSource.LOGGER.error("Failed to save level {}", worldDir, e);
                }
            });
            // Leaf end - Async playerdata saving""",
    """            // Warext - compression and filesystem I/O both happen off the tick thread.
            org.dreeam.leaf.async.AsyncPlayerDataSaving.submit(() -> {
                Path dataFile = null;
                try {
                    dataFile = Files.createTempFile(worldDir, "level", ".dat");
                    NbtIo.writeCompressed(root, dataFile);
                    Path oldDataFile = this.levelDirectory.oldDataFile();
                    Path currentFile = this.levelDirectory.dataFile();
                    Util.safeReplaceFile(currentFile, dataFile, oldDataFile);
                } catch (Exception e) {
                    LevelStorageSource.LOGGER.error("Failed to save level {}", worldDir, e);
                    if (dataFile != null) {
                        try {
                            Files.deleteIfExists(dataFile);
                        } catch (Exception ignored) {
                        }
                    }
                }
            });""",
    "async level compression",
)

player_storage = "leaf-server/src/minecraft/java/net/minecraft/world/level/storage/PlayerDataStorage.java"
replace_once(
    player_storage,
    """        var nbtBytes = new it.unimi.dsi.fastutil.io.FastByteArrayOutputStream(65536);
        try {
            NbtIo.writeCompressed(compoundTag, nbtBytes);
        } catch (Exception exception) {
            LOGGER.warn("Failed to encode player data for {}", stringId, exception);
            return;
        }
        lockFor(uniqueId, playerName);
        synchronized (PlayerDataStorage.this) {
            org.dreeam.leaf.async.AsyncPlayerDataSaving.submit(() -> {
                try {
                    Path playerDirPath = this.playerDir.toPath();
                    Path tmpFile = Files.createTempFile(playerDirPath, stringId + "-", ".dat");
                    org.apache.commons.io.FileUtils.writeByteArrayToFile(tmpFile.toFile(), nbtBytes.array, 0, nbtBytes.length, false);
                    Path realFile = playerDirPath.resolve(stringId + ".dat");
                    Path oldFile = playerDirPath.resolve(stringId + ".dat_old");
                    Util.safeReplaceFile(realFile, tmpFile, oldFile);
                } catch (Exception var7) {
                    LOGGER.warn("Failed to save player data for {}", playerName, var7);
                } finally {
                    synchronized (PlayerDataStorage.this) {
                        savingLocks.remove(uniqueId);
                    }
                }
            }).ifPresent(future -> savingLocks.put(uniqueId, future));
        }""",
    """        lockFor(uniqueId, playerName);
        synchronized (PlayerDataStorage.this) {
            org.dreeam.leaf.async.AsyncPlayerDataSaving.submit(() -> {
                Path tmpFile = null;
                try {
                    Path playerDirPath = this.playerDir.toPath();
                    tmpFile = Files.createTempFile(playerDirPath, stringId + "-", ".dat");
                    NbtIo.writeCompressed(compoundTag, tmpFile);
                    Path realFile = playerDirPath.resolve(stringId + ".dat");
                    Path oldFile = playerDirPath.resolve(stringId + ".dat_old");
                    Util.safeReplaceFile(realFile, tmpFile, oldFile);
                } catch (Exception var7) {
                    LOGGER.warn("Failed to save player data for {}", playerName, var7);
                    if (tmpFile != null) {
                        try {
                            Files.deleteIfExists(tmpFile);
                        } catch (Exception ignored) {
                        }
                    }
                } finally {
                    synchronized (PlayerDataStorage.this) {
                        savingLocks.remove(uniqueId);
                    }
                }
            }).ifPresent(future -> savingLocks.put(uniqueId, future));
        }""",
    "async player compression",
)

print("Stage 2: p99/allocation/I-O optimizations applied.")


# 12) Collision movement: reuse step-up voxel list instead of allocating ArrayList on each stepped collision.
replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/world/EntityCollisionCache.java",
    """public record EntityCollisionCache(
    ObjectArrayList<VoxelShape> potentialCollisionsVoxel,
    ObjectArrayList<AABB> potentialCollisionsBB,
    ObjectArrayList<AABB> entityAABBs
) {
    public EntityCollisionCache() {
        this(new ObjectArrayList<>(), new ObjectArrayList<>(), new ObjectArrayList<>());
    }

    public void clear() {
        potentialCollisionsVoxel.clear();
        potentialCollisionsBB.clear();
        entityAABBs.clear();
    }
}""",
    """public record EntityCollisionCache(
    ObjectArrayList<VoxelShape> potentialCollisionsVoxel,
    ObjectArrayList<AABB> potentialCollisionsBB,
    ObjectArrayList<AABB> entityAABBs,
    ObjectArrayList<VoxelShape> stepVoxels
) {
    public EntityCollisionCache() {
        this(new ObjectArrayList<>(), new ObjectArrayList<>(), new ObjectArrayList<>(), new ObjectArrayList<>());
    }

    public void clear() {
        potentialCollisionsVoxel.clear();
        potentialCollisionsBB.clear();
        entityAABBs.clear();
        stepVoxels.clear();
    }
}""",
    "collision step voxel reusable buffer",
)
replace_once(
    "leaf-server/src/minecraft/java/net/minecraft/world/entity/Entity.java",
    """        final List<VoxelShape> stepVoxels = new ArrayList<>();
        final List<AABB> stepAABBs = entityCollisionCache.entityAABBs();""",
    """        final List<VoxelShape> stepVoxels = entityCollisionCache.stepVoxels();
        final List<AABB> stepAABBs = entityCollisionCache.entityAABBs();""",
    "collision step voxel allocation removal",
)

# 13) AsyncPath publication/callback correctness: eliminate completion-vs-registration races.
async_path = "leaf-server/src/main/java/org/dreeam/leaf/async/path/AsyncPath.java"
replace_once(
    async_path,
    """    private boolean ready = false;

    private final ArrayList<Consumer<Path>> postProcessing = new ArrayList<>();""",
    """    private volatile boolean ready = false;
    private final Object completionLock = new Object();

    private final ArrayList<Consumer<Path>> postProcessing = new ArrayList<>();""",
    "async path completion publication",
)
replace_once(
    async_path,
    """    public void schedulePostProcessing(Consumer<Path> runnable) {
        if (this.ready) {
            runnable.accept(this);
        } else {
            this.postProcessing.add(runnable);
        }
    }""",
    """    public void schedulePostProcessing(Consumer<Path> runnable) {
        boolean invokeNow;
        synchronized (this.completionLock) {
            if (this.ready) {
                invokeNow = true;
            } else {
                this.postProcessing.add(runnable);
                invokeNow = false;
            }
        }
        if (invokeNow) {
            runnable.accept(this);
        }
    }""",
    "async path callback race fix",
)
replace_once(
    async_path,
    """    private void complete(Path bestPath) {
        this.nodes = bestPath.nodes;
        this.target = bestPath.getTarget();
        this.distToTarget = bestPath.getDistToTarget();
        this.canReach = bestPath.canReach();
        Path.DebugData debugData = bestPath.debugData();
        if (debugData != null) {
            this.setDebug(debugData.openSet(), debugData.closedSet(), debugData.targetNodes());
        }
        this.pathFn = null;
        this.ready = true;
        for (Consumer<Path> consumer : this.postProcessing) {
            consumer.accept(this);
        }
        this.postProcessing.clear();
    }""",
    """    private void complete(Path bestPath) {
        final Consumer<Path>[] callbacks;
        synchronized (this.completionLock) {
            if (this.ready) {
                return;
            }
            this.nodes = bestPath.nodes;
            this.target = bestPath.getTarget();
            this.distToTarget = bestPath.getDistToTarget();
            this.canReach = bestPath.canReach();
            Path.DebugData debugData = bestPath.debugData();
            if (debugData != null) {
                this.setDebug(debugData.openSet(), debugData.closedSet(), debugData.targetNodes());
            }
            this.pathFn = null;
            this.ready = true;
            @SuppressWarnings("unchecked")
            Consumer<Path>[] copied = this.postProcessing.toArray(new Consumer[0]);
            callbacks = copied;
            this.postProcessing.clear();
        }
        // Never invoke arbitrary navigation callbacks while holding the completion lock.
        for (Consumer<Path> consumer : callbacks) {
            consumer.accept(this);
        }
    }""",
    "async path idempotent completion",
)

# 14) Despawn nearest-player lookup: reuse coordinate/index buffers every tick.
despawn = "leaf-server/src/main/java/org/dreeam/leaf/world/DespawnMap.java"
replace_once(
    despawn,
    """    private final KDTree3D tree = new KDTree3D();
    private final double[] hard = new double[CATEGORIES.length];
    private final double[] sort = new double[CATEGORIES.length];
    private boolean difficultyIsPeaceful = true;""",
    """    private final KDTree3D tree = new KDTree3D();
    private final double[] hard = new double[CATEGORIES.length];
    private final double[] sort = new double[CATEGORIES.length];
    private double[] playerX = new double[0];
    private double[] playerY = new double[0];
    private double[] playerZ = new double[0];
    private int[] playerIndices = new int[0];
    private final double[][] playerCoords = new double[3][];
    private boolean difficultyIsPeaceful = true;

    private void ensurePlayerCapacity(final int required) {
        if (this.playerX.length >= required) {
            return;
        }
        int capacity = Math.max(8, this.playerX.length);
        while (capacity < required) {
            capacity <<= 1;
        }
        this.playerX = new double[capacity];
        this.playerY = new double[capacity];
        this.playerZ = new double[capacity];
        this.playerIndices = new int[capacity];
        this.playerCoords[0] = this.playerX;
        this.playerCoords[1] = this.playerY;
        this.playerCoords[2] = this.playerZ;
    }""",
    "despawn reusable player buffers",
)
replace_once(
    despawn,
    """        final ServerPlayer[] players = world.players().toArray(EMPTY_PLAYERS);
        final double[] playerX = new double[players.length];
        final double[] playerY = new double[players.length];
        final double[] playerZ = new double[players.length];
        int i = 0;
        for (int j = 0; j < players.length; j++) {
            final ServerPlayer p = players[j];
            if (EntitySelector.PLAYER_AFFECTS_SPAWNING.test(p)) {
                playerX[i] = p.getX();
                playerY[i] = p.getY();
                playerZ[i] = p.getZ();
                players[i] = p;
                i++;
            }
        }
        tree.build(new double[][]{playerX, playerY, playerZ}, new int[i]);""",
    """        final java.util.List<ServerPlayer> players = world.players();
        ensurePlayerCapacity(players.size());
        int i = 0;
        for (int j = 0, playerCount = players.size(); j < playerCount; j++) {
            final ServerPlayer p = players.get(j);
            if (EntitySelector.PLAYER_AFFECTS_SPAWNING.test(p)) {
                this.playerX[i] = p.getX();
                this.playerY[i] = p.getY();
                this.playerZ[i] = p.getZ();
                i++;
            }
        }
        tree.build(this.playerCoords, this.playerIndices, i);""",
    "despawn per-tick allocation removal",
)

# 15) Natural-spawn player lookup: reuse player coordinate arrays and avoid toArray/new KD input arrays.
nature = "leaf-server/src/main/java/org/dreeam/leaf/world/NatureSpawnChunkMap.java"
replace_once(
    nature,
    """    private final LongArrayList[] centersByRadius;
    private final LongSet set;
    private final KDTree3D tree;
    private boolean ready;""",
    """    private final LongArrayList[] centersByRadius;
    private final LongSet set;
    private final KDTree3D tree;
    private double[] playerX = new double[0];
    private double[] playerY = new double[0];
    private double[] playerZ = new double[0];
    private int[] playerIndices = new int[0];
    private final double[][] playerCoords = new double[3][];
    private boolean ready;

    private void ensurePlayerCapacity(final int required) {
        if (this.playerX.length >= required) {
            return;
        }
        int capacity = Math.max(8, this.playerX.length);
        while (capacity < required) {
            capacity <<= 1;
        }
        this.playerX = new double[capacity];
        this.playerY = new double[capacity];
        this.playerZ = new double[capacity];
        this.playerIndices = new int[capacity];
        this.playerCoords[0] = this.playerX;
        this.playerCoords[1] = this.playerY;
        this.playerCoords[2] = this.playerZ;
    }""",
    "natural spawn reusable player buffers",
)
replace_once(
    nature,
    """    public void tick(final ServerLevel world, final List<LevelChunk> out) {
        ServerPlayer[] players = initPlayer(world);
        for (int index = 0; index < SIZE_RADIUS; index++) {
            buildBfs(index);
        }
        buildKdTree(world.purpurConfig.mobSpawningIgnoreCreativePlayers, players);
        collectSpawningChunks(world.getChunkSource().fullChunksNonSync, this.set, out);
        this.ready = true;
    }""",
    """    public void tick(final ServerLevel world, final List<LevelChunk> out) {
        java.util.List<ServerPlayer> players = world.players();
        initPlayer(players);
        for (int index = 0; index < SIZE_RADIUS; index++) {
            buildBfs(index);
        }
        buildKdTree(world.purpurConfig.mobSpawningIgnoreCreativePlayers, players);
        collectSpawningChunks(world.getChunkSource().fullChunksNonSync, this.set, out);
        this.ready = true;
    }""",
    "natural spawn avoid player array snapshot",
)
replace_once(
    nature,
    """    private ServerPlayer[] initPlayer(final ServerLevel world) {
        ServerPlayer[] players = world.players().toArray(EMPTY_PLAYERS);
        for (final ServerPlayer player : players) {
            if (player.isSpectator()) {
                continue;
            }
            PlayerNaturallySpawnCreaturesEvent event = player.playerNaturallySpawnedEvent;
            if (event == null || event.isCancelled()) {
                continue;
            }
            int range = event.getSpawnRadius();
            if (range > MAX_RADIUS) {
                range = MAX_RADIUS;
            } else if (range < 0) {
                continue;
            }
            this.centersByRadius[range].add(player.chunkPosition().longKey());
        }
        return players;
    }

    private void buildKdTree(final boolean ignoreCreativePlayers, final ServerPlayer[] players) {
        double[] pxl = new double[players.length];
        double[] pyl = new double[players.length];
        double[] pzl = new double[players.length];
        int i = 0;
        for (final ServerPlayer p : players) {
            if (!p.isSpectator() && !(ignoreCreativePlayers && p.isCreative())) {
                pxl[i] = p.getX();
                pyl[i] = p.getY();
                pzl[i] = p.getZ();
                i++;
            }
        }
        this.tree.build(new double[][]{pxl, pyl, pzl}, new int[i]);
    }""",
    """    private void initPlayer(final java.util.List<ServerPlayer> players) {
        for (int i = 0, playerCount = players.size(); i < playerCount; i++) {
            final ServerPlayer player = players.get(i);
            if (player.isSpectator()) {
                continue;
            }
            PlayerNaturallySpawnCreaturesEvent event = player.playerNaturallySpawnedEvent;
            if (event == null || event.isCancelled()) {
                continue;
            }
            int range = event.getSpawnRadius();
            if (range > MAX_RADIUS) {
                range = MAX_RADIUS;
            } else if (range < 0) {
                continue;
            }
            this.centersByRadius[range].add(player.chunkPosition().longKey());
        }
    }

    private void buildKdTree(final boolean ignoreCreativePlayers, final java.util.List<ServerPlayer> players) {
        ensurePlayerCapacity(players.size());
        int i = 0;
        for (int j = 0, playerCount = players.size(); j < playerCount; j++) {
            final ServerPlayer p = players.get(j);
            if (!p.isSpectator() && !(ignoreCreativePlayers && p.isCreative())) {
                this.playerX[i] = p.getX();
                this.playerY[i] = p.getY();
                this.playerZ[i] = p.getZ();
                i++;
            }
        }
        this.tree.build(this.playerCoords, this.playerIndices, i);
    }""",
    "natural spawn KD allocation removal",
)

print("Stage 3: collision, AsyncPath correctness, despawn/spawn allocation optimizations applied.")


# 16) Async save scheduler: coalesce superseded saves per key and remove the unbounded duplicate-write pattern.
async_save_class = """package org.dreeam.leaf.async;

import net.minecraft.util.Util;
import org.apache.logging.log4j.LogManager;
import org.apache.logging.log4j.Logger;
import org.dreeam.leaf.config.modules.async.AsyncPlayerDataSave;

import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Future;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.concurrent.atomic.AtomicReference;

public class AsyncPlayerDataSaving {

    private static final Logger LOGGER = LogManager.getLogger("Warext Async Save");
    public static ExecutorService IO_POOL = null;
    private static final ConcurrentHashMap<Object, LatestTask> LATEST_TASKS = new ConcurrentHashMap<>();

    private static final class LatestTask {
        private Runnable latest;
        private boolean scheduled;
        private CompletableFuture<Void> completion = CompletableFuture.completedFuture(null);
    }

    private AsyncPlayerDataSaving() {
    }

    public static synchronized void init() {
        if (IO_POOL == null) {
            IO_POOL = new ThreadPoolExecutor(
                1,
                1,
                0L, TimeUnit.MILLISECONDS,
                new LinkedBlockingQueue<>(),
                new com.google.common.util.concurrent.ThreadFactoryBuilder()
                    .setPriority(Thread.NORM_PRIORITY - 2)
                    .setNameFormat("Warext Server IO Thread")
                    .setUncaughtExceptionHandler(Util::onThreadException)
                    .build(),
                new ThreadPoolExecutor.AbortPolicy()
            );
        }
    }

    private static void ensurePool() {
        if (IO_POOL == null) {
            init();
        }
    }

    public static Optional<Future<?>> submit(Runnable runnable) {
        if (!AsyncPlayerDataSave.enabled) {
            runnable.run();
            return Optional.empty();
        }
        ensurePool();
        return Optional.of(IO_POOL.submit(runnable));
    }

    /**
     * Keeps at most one scheduled drain task per key. If multiple saves arrive while a save is
     * queued/running, only the newest not-yet-started save is retained.
     */
    public static CompletableFuture<Void> submitLatest(final Object key, final Runnable runnable) {
        if (!AsyncPlayerDataSave.enabled) {
            runnable.run();
            return CompletableFuture.completedFuture(null);
        }

        ensurePool();
        final AtomicReference<CompletableFuture<Void>> completionRef = new AtomicReference<>();
        LATEST_TASKS.compute(key, (ignored, existing) -> {
            final LatestTask slot = existing == null ? new LatestTask() : existing;
            synchronized (slot) {
                slot.latest = runnable;
                if (!slot.scheduled) {
                    slot.scheduled = true;
                    slot.completion = new CompletableFuture<>();
                    final LatestTask scheduledSlot = slot;
                    IO_POOL.execute(() -> drainLatest(key, scheduledSlot));
                }
                completionRef.set(slot.completion);
            }
            return slot;
        });
        return completionRef.get();
    }

    private static void drainLatest(final Object key, final LatestTask slot) {
        while (true) {
            final Runnable task;
            synchronized (slot) {
                task = slot.latest;
                slot.latest = null;
                if (task == null) {
                    slot.scheduled = false;
                    slot.completion.complete(null);
                    break;
                }
            }

            try {
                task.run();
            } catch (Throwable throwable) {
                LOGGER.error("Async save task failed for key {}", key, throwable);
            }
        }

        // Remove idle slots without racing a new submit for the same key.
        LATEST_TASKS.compute(key, (ignored, current) -> {
            if (current != slot) {
                return current;
            }
            synchronized (slot) {
                return !slot.scheduled && slot.latest == null ? null : slot;
            }
        });
    }

    /**
     * Waits until all saves known for a key at the time of (and during) the wait are drained.
     */
    public static void awaitLatest(final Object key, final long timeoutMillis)
        throws InterruptedException, ExecutionException, TimeoutException {
        if (!AsyncPlayerDataSave.enabled) {
            return;
        }

        final long deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(timeoutMillis);
        while (true) {
            final LatestTask slot = LATEST_TASKS.get(key);
            if (slot == null) {
                return;
            }

            final CompletableFuture<Void> completion;
            synchronized (slot) {
                if (!slot.scheduled && slot.latest == null) {
                    return;
                }
                completion = slot.completion;
            }

            final long remaining = deadline - System.nanoTime();
            if (remaining <= 0L) {
                throw new TimeoutException("Timed out waiting for async save: " + key);
            }
            completion.get(remaining, TimeUnit.NANOSECONDS);
        }
    }
}
"""
write("leaf-server/src/main/java/org/dreeam/leaf/async/AsyncPlayerDataSaving.java", async_save_class)
print("[ok] coalescing async save scheduler")

# Enable the now-safer async save path by default; users can still disable it in Leaf config.
replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/async/AsyncPlayerDataSave.java",
    "    public static boolean enabled = false;",
    "    public static boolean enabled = true;",
    "async save optimized default",
)

# Coalesce repeated level.dat writes by world path.
replace_once(
    "leaf-server/src/minecraft/java/net/minecraft/world/level/storage/LevelStorageSource.java",
    """            org.dreeam.leaf.async.AsyncPlayerDataSaving.submit(() -> {
                Path dataFile = null;""",
    """            org.dreeam.leaf.async.AsyncPlayerDataSaving.submitLatest(worldDir, () -> {
                Path dataFile = null;""",
    "coalesce level.dat saves",
)

# Player saves no longer wait for the previous disk write before enqueueing. The scheduler keeps
# the running save plus only the newest pending snapshot for the UUID.
player_storage = "leaf-server/src/minecraft/java/net/minecraft/world/level/storage/PlayerDataStorage.java"
replace_once(
    player_storage,
    "    private final java.util.Map<java.util.UUID, java.util.concurrent.Future<?>> savingLocks = new it.unimi.dsi.fastutil.objects.Object2ObjectOpenHashMap<>(); // Leaf - Async playerdata saving\n",
    "",
    "remove racy player save future map",
)

replace_once(
    player_storage,
    """        lockFor(uniqueId, playerName);
        synchronized (PlayerDataStorage.this) {
            org.dreeam.leaf.async.AsyncPlayerDataSaving.submit(() -> {
                Path tmpFile = null;
                try {
                    Path playerDirPath = this.playerDir.toPath();
                    tmpFile = Files.createTempFile(playerDirPath, stringId + "-", ".dat");
                    NbtIo.writeCompressed(compoundTag, tmpFile);
                    Path realFile = playerDirPath.resolve(stringId + ".dat");
                    Path oldFile = playerDirPath.resolve(stringId + ".dat_old");
                    Util.safeReplaceFile(realFile, tmpFile, oldFile);
                } catch (Exception var7) {
                    LOGGER.warn("Failed to save player data for {}", playerName, var7);
                    if (tmpFile != null) {
                        try {
                            Files.deleteIfExists(tmpFile);
                        } catch (Exception ignored) {
                        }
                    }
                } finally {
                    synchronized (PlayerDataStorage.this) {
                        savingLocks.remove(uniqueId);
                    }
                }
            }).ifPresent(future -> savingLocks.put(uniqueId, future));
        }""",
    """        org.dreeam.leaf.async.AsyncPlayerDataSaving.submitLatest(uniqueId, () -> {
            Path tmpFile = null;
            try {
                Path playerDirPath = this.playerDir.toPath();
                tmpFile = Files.createTempFile(playerDirPath, stringId + "-", ".dat");
                NbtIo.writeCompressed(compoundTag, tmpFile);
                Path realFile = playerDirPath.resolve(stringId + ".dat");
                Path oldFile = playerDirPath.resolve(stringId + ".dat_old");
                Util.safeReplaceFile(realFile, tmpFile, oldFile);
            } catch (Exception var7) {
                LOGGER.warn("Failed to save player data for {}", playerName, var7);
                if (tmpFile != null) {
                    try {
                        Files.deleteIfExists(tmpFile);
                    } catch (Exception ignored) {
                    }
                }
            }
        });""",
    "coalesce player saves without main-thread predecessor wait",
)

# Keep load/backup ordering guarantees, but without cancellation/removal races between old/new Futures.
start_marker = "    private void lockFor(final java.util.UUID uniqueId, final String playerName) {"
end_marker = "    // Leaf end - Async playerdata saving"
data = read(player_storage)
start = data.find(start_marker)
if start < 0:
    raise RuntimeError("player save lockFor start not found")
end = data.find(end_marker, start)
if end < 0:
    raise RuntimeError("player save lockFor end not found")
replacement = """    private void lockFor(final java.util.UUID uniqueId, final String playerName) {
        try {
            org.dreeam.leaf.async.AsyncPlayerDataSaving.awaitLatest(uniqueId, 10_000L);
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            LOGGER.warn("Interrupted while waiting for player data save for {}", playerName, exception);
        } catch (java.util.concurrent.ExecutionException | java.util.concurrent.TimeoutException exception) {
            LOGGER.warn("Timed out or failed while waiting for player data save for {}", playerName, exception);
        }
    }
"""
data = data[:start] + replacement + data[end:]
write(player_storage, data)
print("[ok] race-free player save ordering wait")

print("Stage 4: async-save coalescing/backpressure optimization applied.")


# 17) Async tracker: pool TrackerCtx instances and avoid per-tick TrackerSlice object arrays.
tracker_ctx = "leaf-server/src/main/java/org/dreeam/leaf/async/tracker/TrackerCtx.java"
replace_once(
    tracker_ctx,
    """    public TrackerCtx(ServerLevel world) {
        this.world = world;
    }

    public void collectStopSeenByPlayer""",
    """    public TrackerCtx(ServerLevel world) {
        this.world = world;
    }

    public void reset() {
        this.packets.clear();
        this.itemFrames.clear();
        this.stopSeen.clear();
        this.startSeen.clear();
        this.resync.clear();
        this.pluginEntity.clear();
        this.syncAttributes.clear();
        this.updateData.clear();
    }

    public void collectStopSeenByPlayer""",
    "tracker context reusable reset",
)

tracker_task = """package org.dreeam.leaf.async.tracker;

import ca.spottedleaf.moonrise.patches.chunk_system.entity.ChunkSystemEntity;
import ca.spottedleaf.moonrise.patches.chunk_system.level.chunk.ChunkData;
import it.unimi.dsi.fastutil.objects.Reference2ReferenceOpenHashMap;
import net.minecraft.server.level.ChunkMap;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.entity.Entity;

import java.util.concurrent.Callable;

public record TrackerTask(
    ServerLevel world,
    ChunkMap.TrackedEntity[] trackers,
    int start,
    int end,
    Reference2ReferenceOpenHashMap<ChunkMap.TrackedEntity, TrackerInput> inputs,
    Reference2ReferenceOpenHashMap<ChunkMap.TrackedEntity, TrackerInput> cap,
    TrackerCtx context
) implements Callable<TrackerCtx> {

    @Override
    public TrackerCtx call() {
        final TrackerCtx ctx = this.context;
        final ChunkMap.TrackedEntity[] raw = this.trackers;
        for (int i = this.start; i < this.end; i++) {
            final ChunkMap.TrackedEntity tracker = raw[i];
            final Entity entity = tracker.serverEntity.entity;
            if (entity.moonrise$getTrackedEntity() != tracker) {
                continue;
            }
            if (tracker.getClass() != ChunkMap.TrackedEntity.class) {
                ctx.pluginEntity(tracker);
                continue;
            }

            TrackerInput input = inputs.get(tracker);
            TrackerInput patch = cap.get(tracker);
            if (patch != null) {
                input.apply(patch);
            }

            ChunkData chunkData = ((ChunkSystemEntity) entity).moonrise$getChunkData();
            boolean sendChanges = tracker.leaf$tick(ctx, chunkData == null ? null : chunkData.nearbyPlayers);
            if (!sendChanges) {
                net.minecraft.server.level.FullChunkStatus status = entity.moonrise$getChunkStatus();
                sendChanges = status != null && status.isOrAfter(net.minecraft.server.level.FullChunkStatus.ENTITY_TICKING);
            }
            if (sendChanges || entity.needsSync) {
                tracker.serverEntity.leaf$sendChanges(ctx, tracker, input, false);
            }
        }
        return ctx;
    }
}
"""
write("leaf-server/src/main/java/org/dreeam/leaf/async/tracker/TrackerTask.java", tracker_task)
print("[ok] tracker task range form")

async_tracker = "leaf-server/src/main/java/org/dreeam/leaf/async/tracker/AsyncTracker.java"
replace_once(
    async_tracker,
    """    private Reference2ReferenceOpenHashMap<ChunkMap.TrackedEntity, TrackerInput> captureSpare = new Reference2ReferenceOpenHashMap<>();
    private ChunkMap.TrackedEntity[] trackerSnapshot = new ChunkMap.TrackedEntity[0];""",
    """    private Reference2ReferenceOpenHashMap<ChunkMap.TrackedEntity, TrackerInput> captureSpare = new Reference2ReferenceOpenHashMap<>();
    private ChunkMap.TrackedEntity[] trackerSnapshot = new ChunkMap.TrackedEntity[0];
    private final java.util.ArrayDeque<TrackerCtx> trackerCtxPool = new java.util.ArrayDeque<>();""",
    "tracker context pool field",
)

old_slice_block = """        ChunkMap.TrackedEntity[] raw = this.trackerSnapshot;
        System.arraycopy(trackersRef.getRawDataUnchecked(), 0, raw, 0, len);
        TrackerSlice slice = new TrackerSlice(raw, 0, len);

        ThreadPool exec = Objects.requireNonNull(TRACKER_EXECUTOR);
        int min = MultithreadedTracker.minEntitiesPerTask;
        TrackerSlice[] slices = len <= exec.threadCount() * min ? slice.chunks(min) : slice.splitEvenly(exec.threadCount());
        @SuppressWarnings("unchecked")
        Future<TrackerCtx>[] futures = new Future[slices.length];
        for (int i = 0; i < futures.length; i++) {
            futures[i] = exec.submitOrRun(new TrackerTask(world, slices[i], trackers, cap));
        }
        exec.unpark();
        this.fut = futures;"""

new_range_block = """        ChunkMap.TrackedEntity[] raw = this.trackerSnapshot;
        System.arraycopy(trackersRef.getRawDataUnchecked(), 0, raw, 0, len);

        ThreadPool exec = Objects.requireNonNull(TRACKER_EXECUTOR);
        final int min = Math.max(1, MultithreadedTracker.minEntitiesPerTask);
        final int threadCount = Math.max(1, exec.threadCount());
        final boolean chunked = len <= threadCount * min;
        final int taskCount = chunked ? (len + min - 1) / min : Math.min(threadCount, len);

        @SuppressWarnings("unchecked")
        Future<TrackerCtx>[] futures = new Future[taskCount];

        int cursor = 0;
        final int base = chunked ? 0 : len / taskCount;
        final int remainder = chunked ? 0 : len % taskCount;
        for (int i = 0; i < taskCount; i++) {
            final int start;
            final int end;
            if (chunked) {
                start = i * min;
                end = Math.min(start + min, len);
            } else {
                start = cursor;
                final int size = base + (i < remainder ? 1 : 0);
                end = start + size;
                cursor = end;
            }
            TrackerCtx ctx = this.trackerCtxPool.pollFirst();
            if (ctx == null) {
                ctx = new TrackerCtx(world);
            } else {
                ctx.reset();
            }
            futures[i] = exec.submitOrRun(new TrackerTask(world, raw, start, end, trackers, cap, ctx));
        }
        exec.unpark();
        this.fut = futures;"""

replace_once(
    async_tracker,
    old_slice_block,
    new_range_block,
    "tracker slice allocation removal",
)

replace_once(
    async_tracker,
    """    private static void handle(final Future<TrackerCtx>[] futures) {
        try {
            TrackerCtx ctx = futures[0].get();
            @SuppressWarnings("unchecked")
            Object2ObjectOpenHashMap<ServerPlayerConnection, ObjectArrayList<Packet<?>>>[] packets = new Object2ObjectOpenHashMap[futures.length - 1];
            for (int i = 1; i < futures.length; i++) {
                packets[i - 1] = ctx.join(futures[i].get());
            }
            ctx.handle(packets);
        } catch (final InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (final ExecutionException e) {
            throw new RuntimeException(e);
        }
    }""",
    """    private void handle(final Future<TrackerCtx>[] futures) {
        try {
            TrackerCtx ctx = futures[0].get();
            @SuppressWarnings("unchecked")
            Object2ObjectOpenHashMap<ServerPlayerConnection, ObjectArrayList<Packet<?>>>[] packets = new Object2ObjectOpenHashMap[futures.length - 1];
            for (int i = 1; i < futures.length; i++) {
                packets[i - 1] = ctx.join(futures[i].get());
            }
            ctx.handle(packets);

            for (Future<TrackerCtx> future : futures) {
                TrackerCtx completed = future.get();
                completed.reset();
                this.trackerCtxPool.addLast(completed);
            }
        } catch (final InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (final ExecutionException e) {
            throw new RuntimeException(e);
        }
    }""",
    "tracker context recycling",
)

# Remove the now-unused TrackerSlice import.
data = read(async_tracker)
data = data.replace("import org.dreeam.leaf.util.TrackerSlice;\\n", "")
write(async_tracker, data)
print("[ok] tracker slice import cleanup")

print("Stage 5: tracker context/range allocation optimization applied.")


# 18) KD-tree adaptive small-N fast path: linear scan avoids build/partial-sort overhead on tiny player sets.
def patch_kd_linear(rel, dims):
    data = read(rel)
    if dims == 2:
        field_old = "    private int[] nil = EMPTY_INTS;"
        field_new = """    private int[] nil = EMPTY_INTS;
    private static final int LINEAR_THRESHOLD = Math.max(0, Integer.getInteger("warext.kdtree.linear-threshold", 8));
    private int linearLength;
    private double[] linearX = EMPTY_DOUBLES;
    private double[] linearY = EMPTY_DOUBLES;"""
        empty_old = """        if (length == 0 || coords.length != 2) {
            ensureSearch(0, 0);
            return;
        }
        if (length < 0 || length > indices.length) throw new IllegalArgumentException("Invalid KD-tree length");
        for (int i = 0; i < length; i++) {"""
        empty_new = """        if (length == 0 || coords.length != 2) {
            this.linearLength = 0;
            this.linearX = EMPTY_DOUBLES;
            this.linearY = EMPTY_DOUBLES;
            ensureSearch(0, 0);
            return;
        }
        if (length < 0 || length > indices.length) throw new IllegalArgumentException("Invalid KD-tree length");
        if (LINEAR_THRESHOLD > 0 && length <= LINEAR_THRESHOLD) {
            this.linearLength = length;
            this.linearX = coords[0];
            this.linearY = coords[1];
            ensureSearch(0, 0);
            return;
        }
        this.linearLength = 0;
        this.linearX = EMPTY_DOUBLES;
        this.linearY = EMPTY_DOUBLES;
        for (int i = 0; i < length; i++) {"""
        empty_check_old = """    public boolean isEmpty() {
        return this.search.length == 0 || this.search[0] == SENTINEL;
    }"""
        empty_check_new = """    public boolean isEmpty() {
        return this.linearLength == 0 && (this.search.length == 0 || this.search[0] == SENTINEL);
    }"""
        nearest_old = """    public double nearestSqr(final double tx, final double ty, double dist) {
        final int[] stack = this.search;"""
        nearest_new = """    public double nearestSqr(final double tx, final double ty, double dist) {
        if (this.linearLength != 0) {
            final double[] x = this.linearX;
            final double[] y = this.linearY;
            for (int i = 0; i < this.linearLength; i++) {
                final double dx = x[i] - tx;
                final double dy = y[i] - ty;
                dist = Math.min(dist, euclideanDistanceSquared(dx, dy));
            }
            return dist;
        }
        final int[] stack = this.search;"""
        idx_old = """    public int nearestIdx(final double tx, final double ty, double dist) {
        final int[] stack = this.search;"""
        idx_new = """    public int nearestIdx(final double tx, final double ty, double dist) {
        if (this.linearLength != 0) {
            int nearest = -1;
            final double[] x = this.linearX;
            final double[] y = this.linearY;
            for (int i = 0; i < this.linearLength; i++) {
                final double dx = x[i] - tx;
                final double dy = y[i] - ty;
                final double candidate = euclideanDistanceSquared(dx, dy);
                if (candidate < dist) {
                    dist = candidate;
                    nearest = i;
                }
            }
            return nearest;
        }
        final int[] stack = this.search;"""
    else:
        field_old = "    private int[] nil = EMPTY_INTS;"
        field_new = """    private int[] nil = EMPTY_INTS;
    private static final int LINEAR_THRESHOLD = Math.max(0, Integer.getInteger("warext.kdtree.linear-threshold", 8));
    private int linearLength;
    private double[] linearX = EMPTY_DOUBLES;
    private double[] linearY = EMPTY_DOUBLES;
    private double[] linearZ = EMPTY_DOUBLES;"""
        empty_old = """        if (length == 0 || coords.length != 3) {
            ensureSearch(0, 0);
            return;
        }
        if (length < 0 || length > indices.length) throw new IllegalArgumentException("Invalid KD-tree length");
        for (int i = 0; i < length; i++) {"""
        empty_new = """        if (length == 0 || coords.length != 3) {
            this.linearLength = 0;
            this.linearX = EMPTY_DOUBLES;
            this.linearY = EMPTY_DOUBLES;
            this.linearZ = EMPTY_DOUBLES;
            ensureSearch(0, 0);
            return;
        }
        if (length < 0 || length > indices.length) throw new IllegalArgumentException("Invalid KD-tree length");
        if (LINEAR_THRESHOLD > 0 && length <= LINEAR_THRESHOLD) {
            this.linearLength = length;
            this.linearX = coords[0];
            this.linearY = coords[1];
            this.linearZ = coords[2];
            ensureSearch(0, 0);
            return;
        }
        this.linearLength = 0;
        this.linearX = EMPTY_DOUBLES;
        this.linearY = EMPTY_DOUBLES;
        this.linearZ = EMPTY_DOUBLES;
        for (int i = 0; i < length; i++) {"""
        empty_check_old = """    public boolean isEmpty() {
        return this.search.length == 0 || this.search[0] == SENTINEL;
    }"""
        empty_check_new = """    public boolean isEmpty() {
        return this.linearLength == 0 && (this.search.length == 0 || this.search[0] == SENTINEL);
    }"""
        nearest_old = """    public double nearestSqr(final double tx, final double ty, final double tz, double dist) {
        final int[] stack = this.search;"""
        nearest_new = """    public double nearestSqr(final double tx, final double ty, final double tz, double dist) {
        if (this.linearLength != 0) {
            final double[] x = this.linearX;
            final double[] y = this.linearY;
            final double[] z = this.linearZ;
            for (int i = 0; i < this.linearLength; i++) {
                final double dx = x[i] - tx;
                final double dy = y[i] - ty;
                final double dz = z[i] - tz;
                dist = Math.min(dist, euclideanDistanceSquared(dx, dy, dz));
            }
            return dist;
        }
        final int[] stack = this.search;"""
        idx_old = """    public int nearestIdx(final double tx, final double ty, final double tz, double dist) {
        final int[] stack = this.search;"""
        idx_new = """    public int nearestIdx(final double tx, final double ty, final double tz, double dist) {
        if (this.linearLength != 0) {
            int nearest = -1;
            final double[] x = this.linearX;
            final double[] y = this.linearY;
            final double[] z = this.linearZ;
            for (int i = 0; i < this.linearLength; i++) {
                final double dx = x[i] - tx;
                final double dy = y[i] - ty;
                final double dz = z[i] - tz;
                final double candidate = euclideanDistanceSquared(dx, dy, dz);
                if (candidate < dist) {
                    dist = candidate;
                    nearest = i;
                }
            }
            return nearest;
        }
        final int[] stack = this.search;"""

    for old, new, label in [
        (field_old, field_new, "fields"),
        (empty_old, empty_new, "build fast path"),
        (empty_check_old, empty_check_new, "empty check"),
        (nearest_old, nearest_new, "nearest fast path"),
        (idx_old, idx_new, "nearest index fast path"),
    ]:
        count = data.count(old)
        if count != 1:
            raise RuntimeError(f"KDTree{dims}D {label}: expected 1 match, got {count}")
        data = data.replace(old, new, 1)
    write(rel, data)
    print(f"[ok] KDTree{dims}D adaptive linear fast path")

patch_kd_linear("leaf-server/src/main/java/org/dreeam/leaf/util/KDTree2D.java", 2)
patch_kd_linear("leaf-server/src/main/java/org/dreeam/leaf/util/KDTree3D.java", 3)

print("Stage 6: adaptive small-player spatial lookup optimization applied.")


# 19) Async pathfinding correctness (Leaf PR #921 concepts ported to 26.3):
#     - do not force pending POI paths onto the server thread
#     - revalidate POI state after asynchronous calculation
acquire_poi = "leaf-server/src/minecraft/java/net/minecraft/world/entity/ai/behavior/AcquirePoi.java"
data = read(acquire_poi)

if data.count("pending.process();") != 1:
    raise RuntimeError(f"AcquirePoi pending.process: expected 1 match, got {data.count('pending.process();')}")
data = data.replace(
    "pending.process();",
    "if (!pending.isProcessed()) return false; // Warext - keep async work off the tick thread",
    1,
)

old_pending_call = "processPath(poiType, onPoiAcquisitionEvent, batchCache, level, body, memoryToAcquire, timestamp, level.getPoiManager(), stateSet, pending, random);"
if data.count(old_pending_call) != 1:
    raise RuntimeError(f"AcquirePoi pending processPath: expected 1 match, got {data.count(old_pending_call)}")
data = data.replace(old_pending_call, old_pending_call[:-2] + ", validPoi);", 1)

old_direct_call = "processPath(poiType, onPoiAcquisitionEvent, batchCache, level, body, memoryToAcquire, timestamp, poiManager, poiPositions, path, random);"
if data.count(old_direct_call) != 1:
    raise RuntimeError(f"AcquirePoi direct processPath: expected 1 match, got {data.count(old_direct_call)}")
data = data.replace(old_direct_call, old_direct_call[:-2] + ", validPoi);", 1)

# Add validator parameter only to the extracted processPath method.
method_pos = data.find("private static void processPath(")
if method_pos < 0:
    raise RuntimeError("AcquirePoi processPath method not found")
sig_end = data.find(") {", method_pos)
if sig_end < 0:
    raise RuntimeError("AcquirePoi processPath signature end not found")
signature = data[method_pos:sig_end]
needle = "final RandomSource random"
if needle not in signature:
    raise RuntimeError("AcquirePoi processPath RandomSource parameter not found")
signature_new = signature.replace(
    needle,
    needle + ",\n                                    final BiPredicate<ServerLevel, BlockPos> validPoi",
    1,
)
data = data[:method_pos] + signature_new + data[sig_end:]

# Revalidate and atomically claim the POI after the async result is ready.
method_pos = data.find("private static void processPath(")
method_end = data.find("// Kaiiju end - petal - async path processing", method_pos)
segment = data[method_pos:method_end]
old_body = """BlockPos targetPos = path.getTarget();
            poiManager.getType(targetPos).ifPresent(type -> {
                poiManager.take(poiType, (t, poiPos) -> poiPos.equals(targetPos), targetPos, 1);
                walkTarget.getBrain().setMemory(memoryToAcquire, GlobalPos.of(level.dimension(), targetPos));
                onPoiAcquisitionEvent.ifPresent(event -> level.broadcastEntityEvent(walkTarget, event));
                batchCache.clear();
                level.debugSynchronizers().updatePoi(targetPos);
            });"""
new_body = """BlockPos targetPos = path.getTarget();
            if (!validPoi.test(level, targetPos)) return; // Warext - stale async result
            poiManager.take(poiType, (t, poiPos) -> poiPos.equals(targetPos), targetPos, 1).ifPresent(acquiredPos -> {
                walkTarget.getBrain().setMemory(memoryToAcquire, GlobalPos.of(level.dimension(), acquiredPos));
                onPoiAcquisitionEvent.ifPresent(event -> level.broadcastEntityEvent(walkTarget, event));
                batchCache.clear();
                level.debugSynchronizers().updatePoi(acquiredPos);
            });"""
if segment.count(old_body) != 1:
    raise RuntimeError(f"AcquirePoi result body: expected 1 match, got {segment.count(old_body)}")
segment = segment.replace(old_body, new_body, 1)
data = data[:method_pos] + segment + data[method_end:]
write(acquire_poi, data)
print("[ok] async POI nonblocking wait + result revalidation")

home = "leaf-server/src/minecraft/java/net/minecraft/world/entity/ai/behavior/SetClosestHomeAsWalkTarget.java"
data = read(home)
if data.count("pending.process();") != 1:
    raise RuntimeError(f"Home pending.process: expected 1 match, got {data.count('pending.process();')}")
data = data.replace(
    "pending.process();",
    "if (!pending.isProcessed()) return false; // Warext - keep async work off the tick thread",
    1,
)

# Once a pending result is consumed, do not immediately schedule another path in the same behavior trigger.
pending_call = "processPath(speedModifier, batchCache, lastUpdate, body, level, level.getPoiManager(), stateInt, pending);"
if data.count(pending_call) != 1:
    raise RuntimeError(f"Home pending processPath: expected 1 match, got {data.count(pending_call)}")
data = data.replace(
    pending_call + "\n            pending = null;",
    pending_call + "\n            pending = null;\n            return true;",
    1,
)

method_pos = data.find("private static void processPath(")
if method_pos < 0:
    raise RuntimeError("Home processPath method not found")
method_end = data.find("// Kaiiju end - petal - async path processing", method_pos)
segment = data[method_pos:method_end]
old_type = """Optional<Holder<PoiType>> type = poiManager.getType(targetPos);
            if (type.isPresent()) {"""
new_type = """Optional<Holder<PoiType>> type = poiManager.getType(targetPos);
            if (type.isPresent() && type.get().is(PoiTypes.HOME)) { // Warext - revalidate stale async result"""
if segment.count(old_type) != 1:
    raise RuntimeError(f"Home POI validation: expected 1 match, got {segment.count(old_type)}")
segment = segment.replace(old_type, new_type, 1)
data = data[:method_pos] + segment + data[method_end:]
write(home, data)
print("[ok] async HOME nonblocking wait + result revalidation")

print("Stage 7: async pathfinding POI correctness/p99 fixes applied.")

# 20) Tracker interpolation: accumulate predicted movement in primitives instead of allocating Vec3
#     on every movement event / merge. Materialize at most one Vec3 when interpolation consumes it.
tracker_input = """package org.dreeam.leaf.async.tracker;

import net.minecraft.world.phys.Vec3;
import org.jspecify.annotations.NullMarked;

@NullMarked
public final class TrackerInput {
    public Vec3 trackingPosition;
    private double predictedX;
    private double predictedY;
    private double predictedZ;
    public boolean syncPosition;

    public TrackerInput(final Vec3 trackingPosition, final Vec3 predictedDelta, final boolean syncPosition) {
        this.trackingPosition = trackingPosition;
        this.predictedX = predictedDelta.x;
        this.predictedY = predictedDelta.y;
        this.predictedZ = predictedDelta.z;
        this.syncPosition = syncPosition;
    }

    public void applyPredictedMovement(final Vec3 delta) {
        this.predictedX += delta.x;
        this.predictedY += delta.y;
        this.predictedZ += delta.z;
    }

    void apply(final TrackerInput v) {
        this.trackingPosition = v.trackingPosition;
        this.predictedX += v.predictedX;
        this.predictedY += v.predictedY;
        this.predictedZ += v.predictedZ;
        if (v.syncPosition) {
            this.syncPosition = true;
        }
    }

    public Vec3 consumePredictedDelta() {
        final double x = this.predictedX;
        final double y = this.predictedY;
        final double z = this.predictedZ;
        this.predictedX = 0.0D;
        this.predictedY = 0.0D;
        this.predictedZ = 0.0D;
        if (x == 0.0D && y == 0.0D && z == 0.0D) {
            return Vec3.ZERO;
        }
        return new Vec3(x, y, z);
    }
}
"""
write("leaf-server/src/main/java/org/dreeam/leaf/async/tracker/TrackerInput.java", tracker_input)
print("[ok] primitive tracker predicted-delta accumulator")

replace_once(
    "leaf-server/src/minecraft/java/net/minecraft/world/entity/SteppedInterpolationTracker.java",
    """        final Vec3 predictedDelta = input.predictedDelta;
        if (predictedDelta.lengthSqr() > 1.0E-5F) {
            this.trackedSteps.replaceAll(step -> step.addDelta(predictedDelta));
        }

        input.predictedDelta = Vec3.ZERO;""",
    """        final Vec3 predictedDelta = input.consumePredictedDelta();
        if (predictedDelta.lengthSqr() > 1.0E-5F) {
            this.trackedSteps.replaceAll(step -> step.addDelta(predictedDelta));
        }""",
    "tracker interpolation primitive delta consumption",
)

print("Stage 8: primitive tracker interpolation accumulation applied.")


# 21) Shared Warext CPU budget: pathfinding/tracker auto mode draw from the same conservative
#     processor budget instead of independently scaling to the machine size.
cpu_budget = """package org.dreeam.leaf.performance;

public final class WarextCpuBudget {

    private static final int PROCESSORS = Math.max(1, Runtime.getRuntime().availableProcessors());

    private WarextCpuBudget() {
    }

    public static int processors() {
        return PROCESSORS;
    }

    public static int reservedCores() {
        final int property = Integer.getInteger("warext.cpu.reserve", -1);
        if (property >= 0) {
            return Math.min(property, Math.max(0, PROCESSORS - 1));
        }
        if (PROCESSORS >= 24) return 5;
        if (PROCESSORS >= 16) return 4;
        if (PROCESSORS >= 8) return 2;
        return 1;
    }

    public static int workerBudget() {
        return Math.max(1, PROCESSORS - reservedCores());
    }

    public static int pathfindingThreads() {
        final int override = Integer.getInteger("warext.cpu.pathfinding-threads", 0);
        if (override > 0) return Math.max(1, override);

        final int budget = workerBudget();
        return Math.max(1, Math.min(8, (budget * 35 + 99) / 100));
    }

    public static int trackerThreads() {
        final int override = Integer.getInteger("warext.cpu.tracker-threads", 0);
        if (override > 0) return Math.max(1, override);

        final int budget = workerBudget();
        int path = pathfindingThreads();
        int tracker = Math.max(1, Math.min(8, (budget * 35 + 99) / 100));

        // Keep automatic path + tracker allocations inside the shared worker budget where possible.
        if (budget > 1 && path + tracker > budget) {
            tracker = Math.max(1, budget - path);
        }
        return tracker;
    }

    public static int backgroundHeadroom() {
        return Math.max(0, workerBudget() - pathfindingThreads() - trackerThreads());
    }
}
"""
budget_path = root / "leaf-server/src/main/java/org/dreeam/leaf/performance/WarextCpuBudget.java"
budget_path.parent.mkdir(parents=True, exist_ok=True)
budget_path.write_text(cpu_budget, encoding="utf-8")
print("[ok] shared Warext CPU budget")

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/async/AsyncPathfinding.java",
    """        if (asyncPathfindingMaxThreads <= 0) {
            final int reservedCores = availableProcessors >= 12 ? 3 : (availableProcessors >= 6 ? 2 : 1);
            final int workerBudget = Math.max(1, availableProcessors - reservedCores);
            asyncPathfindingMaxThreads = Math.max(1, Math.min(6, workerBudget / 3));
        }""",
    """        if (asyncPathfindingMaxThreads <= 0) {
            asyncPathfindingMaxThreads = org.dreeam.leaf.performance.WarextCpuBudget.pathfindingThreads();
        }""",
    "pathfinding shared CPU budget",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/async/MultithreadedTracker.java",
    """        if (threads <= 0) {
            final int availableProcessors = Runtime.getRuntime().availableProcessors();
            final int reservedCores = availableProcessors >= 12 ? 3 : (availableProcessors >= 6 ? 2 : 1);
            final int workerBudget = Math.max(1, availableProcessors - reservedCores);
            threads = Math.max(1, Math.min(6, workerBudget / 2));
        }""",
    """        if (threads <= 0) {
            threads = org.dreeam.leaf.performance.WarextCpuBudget.trackerThreads();
        }""",
    "tracker shared CPU budget",
)

print("Stage 9: shared adaptive multi-core CPU budget applied.")

# VoxelBench AI tail-latency: on a 4-vCPU balanced host keep one steady pathfinding worker
# but allow a second low-priority worker when the bounded queue saturates. This avoids immediately
# pushing rejected CPU-heavy path work back onto the tick thread through CALLER_RUNS.
replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/async/path/AsyncPathProcessor.java",
    """    private static int getCorePoolSize() {
        return getMaxPoolSize();
    }""",
    """    private static int getCorePoolSize() {
        if (!org.dreeam.leaf.performance.WarextPerformanceProfile.isExtreme()
            && org.dreeam.leaf.performance.WarextCpuBudget.processors() <= 4) {
            return Math.min(1, getMaxPoolSize());
        }
        return getMaxPoolSize();
    }""",
    "4-vCPU balanced steady/burst pathfinding split",
)


# 22) Async save/compression worker count: draw only from spare shared CPU budget.
#     Keep resource use at one worker on smaller hosts; allow up to two low-priority workers
#     when the machine has genuine async headroom, and let them time out when idle.
budget_file = "leaf-server/src/main/java/org/dreeam/leaf/performance/WarextCpuBudget.java"
replace_once(
    budget_file,
    """    public static int backgroundHeadroom() {
        return Math.max(0, workerBudget() - pathfindingThreads() - trackerThreads());
    }""",
    """    public static int backgroundHeadroom() {
        return Math.max(0, workerBudget() - pathfindingThreads() - trackerThreads());
    }

    public static int ioWorkers() {
        final int override = Integer.getInteger("warext.cpu.io-workers", 0);
        if (override > 0) {
            return Math.max(1, Math.min(4, override));
        }

        final int spare = backgroundHeadroom();
        if (PROCESSORS >= 12 && spare >= 2) {
            return 2;
        }
        return 1;
    }""",
    "shared CPU budget IO worker allocation",
)

async_save = "leaf-server/src/main/java/org/dreeam/leaf/async/AsyncPlayerDataSaving.java"
replace_once(
    async_save,
    """            IO_POOL = new ThreadPoolExecutor(
                1,
                1,
                0L, TimeUnit.MILLISECONDS,
                new LinkedBlockingQueue<>(),
                new com.google.common.util.concurrent.ThreadFactoryBuilder()
                    .setPriority(Thread.NORM_PRIORITY - 2)
                    .setNameFormat("Warext Server IO Thread")
                    .setUncaughtExceptionHandler(Util::onThreadException)
                    .build(),
                new ThreadPoolExecutor.AbortPolicy()
            );""",
    """            final int ioWorkers = org.dreeam.leaf.performance.WarextCpuBudget.ioWorkers();
            final ThreadPoolExecutor executor = new ThreadPoolExecutor(
                ioWorkers,
                ioWorkers,
                30L, TimeUnit.SECONDS,
                new LinkedBlockingQueue<>(),
                new com.google.common.util.concurrent.ThreadFactoryBuilder()
                    .setPriority(Thread.NORM_PRIORITY - 2)
                    .setNameFormat("Warext Server IO Thread-%d")
                    .setUncaughtExceptionHandler(Util::onThreadException)
                    .build(),
                new ThreadPoolExecutor.AbortPolicy()
            );
            executor.allowCoreThreadTimeOut(true);
            IO_POOL = executor;""",
    "adaptive async save IO workers",
)

print("Stage 10: adaptive low-priority save/compression workers applied.")


# 23) NodeEvaluator pooling: remove the global synchronized bottleneck while preserving
#     generator identity and feature isolation. Evaluators may be prepared on one thread and
#     returned on a worker thread, so a thread-local pool is intentionally not used.
node_cache_concurrent = """package org.dreeam.leaf.async.path;

import net.minecraft.world.level.pathfinder.BinaryHeap;
import net.minecraft.world.level.pathfinder.Node;
import net.minecraft.world.level.pathfinder.NodeEvaluator;
import org.apache.commons.lang3.Validate;

import java.util.concurrent.ConcurrentHashMap;
import java.util.concurrent.ConcurrentLinkedQueue;

public final class NodeEvaluatorCache {

    private static final ConcurrentHashMap<PoolKey, ConcurrentLinkedQueue<NodeEvaluator>> NODE_EVALUATORS = new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<IdentityKey<NodeEvaluator>, ConcurrentLinkedQueue<NodeEvaluator>> NODE_EVALUATOR_TO_POOL = new ConcurrentHashMap<>();

    public static final ThreadLocal<BinaryHeap> HEAP_LOCAL = ThreadLocal.withInitial(BinaryHeap::new);
    public static final ThreadLocal<Node[]> NEIGHBORS_LOCAL = ThreadLocal.withInitial(() -> new Node[32]);

    private NodeEvaluatorCache() {
    }

    public static NodeEvaluator takeNodeEvaluator(final NodeEvaluatorGenerator generator, final NodeEvaluator localNodeEvaluator) {
        final int features = NodeEvaluatorFeatures.fromNodeEvaluator(localNodeEvaluator);
        final PoolKey key = new PoolKey(generator, features);
        final ConcurrentLinkedQueue<NodeEvaluator> pool =
            NODE_EVALUATORS.computeIfAbsent(key, ignored -> new ConcurrentLinkedQueue<>());

        NodeEvaluator nodeEvaluator = pool.poll();
        if (nodeEvaluator == null) {
            nodeEvaluator = generator.generate(NodeEvaluatorFeatures.unpack(features));
        }

        final ConcurrentLinkedQueue<NodeEvaluator> previous =
            NODE_EVALUATOR_TO_POOL.put(new IdentityKey<>(nodeEvaluator), pool);
        Validate.isTrue(previous == null, "NodeEvaluator checked out twice");
        return nodeEvaluator;
    }

    public static void returnNodeEvaluator(final NodeEvaluator nodeEvaluator) {
        final ConcurrentLinkedQueue<NodeEvaluator> pool =
            NODE_EVALUATOR_TO_POOL.remove(new IdentityKey<>(nodeEvaluator));
        Validate.notNull(pool, "NodeEvaluator already returned");
        pool.offer(nodeEvaluator);
    }

    public static void removeNodeEvaluator(final NodeEvaluator nodeEvaluator) {
        NODE_EVALUATOR_TO_POOL.remove(new IdentityKey<>(nodeEvaluator));
    }

    private static final class PoolKey {
        private final NodeEvaluatorGenerator generator;
        private final int features;
        private final int hash;

        private PoolKey(final NodeEvaluatorGenerator generator, final int features) {
            this.generator = generator;
            this.features = features;
            this.hash = 31 * System.identityHashCode(generator) + features;
        }

        @Override
        public int hashCode() {
            return this.hash;
        }

        @Override
        public boolean equals(final Object object) {
            return this == object
                || object instanceof PoolKey other
                && this.generator == other.generator
                && this.features == other.features;
        }
    }

    private static final class IdentityKey<T> {
        private final T value;
        private final int hash;

        private IdentityKey(final T value) {
            this.value = value;
            this.hash = System.identityHashCode(value);
        }

        @Override
        public int hashCode() {
            return this.hash;
        }

        @Override
        public boolean equals(final Object object) {
            return this == object
                || object instanceof IdentityKey<?> other
                && this.value == other.value;
        }
    }
}
"""
write("leaf-server/src/main/java/org/dreeam/leaf/async/path/NodeEvaluatorCache.java", node_cache_concurrent)
print("[ok] lock-free concurrent NodeEvaluator pools")

print("Stage 11: concurrent NodeEvaluator pooling applied.")


# 24) Async tracker packet batching: recycle per-connection packet lists instead of allocating
#     fresh backing arrays every tick. Pools are per TrackerCtx and never retain connections.
tracker_ctx = "leaf-server/src/main/java/org/dreeam/leaf/async/tracker/TrackerCtx.java"

replace_once(
    tracker_ctx,
    """    @SuppressWarnings("unchecked")
    private static final Object2ObjectFunction<ServerPlayerConnection, ObjectArrayList<Packet<?>>> INIT_PACKET_LIST = x -> ObjectArrayList.wrap(new Packet[16], 0);
    private final Object2ObjectOpenHashMap<ServerPlayerConnection, ObjectArrayList<Packet<?>>> packets = new Object2ObjectOpenHashMap<>();""",
    """    private static final int PACKET_LIST_POOL_LIMIT = 64;
    private final Object2ObjectOpenHashMap<ServerPlayerConnection, ObjectArrayList<Packet<?>>> packets = new Object2ObjectOpenHashMap<>();
    private final java.util.ArrayDeque<ObjectArrayList<Packet<?>>> packetListPool = new java.util.ArrayDeque<>();""",
    "tracker packet-list pool fields",
)

replace_once(
    tracker_ctx,
    """    public void reset() {
        this.packets.clear();
        this.itemFrames.clear();""",
    """    public void reset() {
        this.recyclePacketLists(false);
        this.itemFrames.clear();""",
    "tracker reset recycles packet buffers",
)

replace_once(
    tracker_ctx,
    """    public void send(ServerPlayerConnection connection, Packet<?> packet) {
        packets.computeIfAbsent(connection, INIT_PACKET_LIST).add(packet);
    }""",
    """    public void send(ServerPlayerConnection connection, Packet<?> packet) {
        ObjectArrayList<Packet<?>> list = packets.get(connection);
        if (list == null) {
            list = this.packetListPool.pollFirst();
            if (list == null) {
                @SuppressWarnings("unchecked")
                final Packet<?>[] backing = new Packet[16];
                list = ObjectArrayList.wrap(backing, 0);
            }
            packets.put(connection, list);
        }
        list.add(packet);
    }""",
    "tracker packet-list reuse on send",
)

replace_once(
    tracker_ctx,
    """    Object2ObjectOpenHashMap<ServerPlayerConnection, ObjectArrayList<Packet<?>>> join(TrackerCtx other) {
        itemFrames.addAll(other.itemFrames);
        stopSeen.addAll(other.stopSeen);
        startSeen.addAll(other.startSeen);
        pluginEntity.addAll(other.pluginEntity);
        resync.addAll(other.resync);
        syncAttributes.addAll(other.syncAttributes);
        updateData.addAll(other.updateData);
        return other.packets;
    }

    void handle(Object2ObjectOpenHashMap<ServerPlayerConnection, ObjectArrayList<Packet<?>>>[] other) {""",
    """    TrackerCtx join(TrackerCtx other) {
        itemFrames.addAll(other.itemFrames);
        stopSeen.addAll(other.stopSeen);
        startSeen.addAll(other.startSeen);
        pluginEntity.addAll(other.pluginEntity);
        resync.addAll(other.resync);
        syncAttributes.addAll(other.syncAttributes);
        updateData.addAll(other.updateData);
        return other;
    }

    void handle(TrackerCtx[] other) {""",
    "tracker joins retain owning packet pools",
)

replace_once(
    tracker_ctx,
    """        for (Object2ObjectOpenHashMap<ServerPlayerConnection, ObjectArrayList<Packet<?>>> otherPackets : other) {
            flush(world, otherPackets);
        }""",
    """        for (TrackerCtx otherContext : other) {
            otherContext.flushPackets();
        }""",
    "tracker flushes through owning contexts",
)

replace_once(
    tracker_ctx,
    """        flush(world, this.packets);
        if (!stopSeen.isEmpty()) {""",
    """        this.flushPackets();
        if (!stopSeen.isEmpty()) {""",
    "tracker primary packet flush reuse",
)

replace_once(
    tracker_ctx,
    """        flush(world, this.packets);
    }""",
    """        this.flushPackets();
    }""",
    "tracker final packet flush reuse",
)

replace_once(
    tracker_ctx,
    """    private static void flush(ServerLevel world, Object2ObjectOpenHashMap<ServerPlayerConnection, ObjectArrayList<Packet<?>>> packets) {
        if (packets.isEmpty()) {
            return;
        }
        packets.forEach((conn, list) -> sendPacket(world, conn, list));
        packets.clear();
    }""",
    """    private void flushPackets() {
        if (this.packets.isEmpty()) {
            return;
        }
        this.recyclePacketLists(true);
    }

    private void recyclePacketLists(final boolean send) {
        if (this.packets.isEmpty()) {
            return;
        }
        this.packets.forEach((conn, list) -> {
            if (send) {
                sendPacket(this.world, conn, list);
            }
            list.clear();
            if (this.packetListPool.size() < PACKET_LIST_POOL_LIMIT) {
                this.packetListPool.addLast(list);
            }
        });
        this.packets.clear();
    }""",
    "tracker packet-list recycling flush",
)

# Remove import made obsolete by eliminating computeIfAbsent factory.
data = read(tracker_ctx)
data = data.replace("import it.unimi.dsi.fastutil.objects.Object2ObjectFunction;\\n", "")
write(tracker_ctx, data)
print("[ok] tracker packet-list factory import cleanup")

async_tracker = "leaf-server/src/main/java/org/dreeam/leaf/async/tracker/AsyncTracker.java"
replace_once(
    async_tracker,
    """            @SuppressWarnings("unchecked")
            Object2ObjectOpenHashMap<ServerPlayerConnection, ObjectArrayList<Packet<?>>>[] packets = new Object2ObjectOpenHashMap[futures.length - 1];
            for (int i = 1; i < futures.length; i++) {
                packets[i - 1] = ctx.join(futures[i].get());
            }
            ctx.handle(packets);""",
    """            TrackerCtx[] contexts = new TrackerCtx[futures.length - 1];
            for (int i = 1; i < futures.length; i++) {
                contexts[i - 1] = ctx.join(futures[i].get());
            }
            ctx.handle(contexts);""",
    "tracker context-owned packet flush array",
)

# These imports were only needed for the old packet-map array in AsyncTracker.
data = read(async_tracker)
data = data.replace("import it.unimi.dsi.fastutil.objects.Object2ObjectOpenHashMap;\\n", "")
data = data.replace("import it.unimi.dsi.fastutil.objects.ObjectArrayList;\\n", "")
data = data.replace("import net.minecraft.network.protocol.Packet;\\n", "")
data = data.replace("import net.minecraft.server.network.ServerPlayerConnection;\\n", "")
write(async_tracker, data)
print("[ok] tracker packet-map import cleanup")

print("Stage 12: reusable tracker packet batching buffers applied.")


# 25) Precipitation random-tick optimization.
# Ported from Leaf PR #854 (GPL-3.0-only).
# Original patch author attribution preserved per the upstream patch:
# HaHaWTH <102713261+HaHaWTH@users.noreply.github.com>
server_level = "leaf-server/src/minecraft/java/net/minecraft/server/level/ServerLevel.java"

replace_once(
    server_level,
    """                this.tickPrecipitation(this.getBlockRandomPos(minX, 0, minZ, 15));""",
    """                this.tickPrecipitation(this.getBlockRandomPos(minX, 0, minZ, 15), chunk); // Warext - Leaf PR #854 precipitation lookup reuse""",
    "precipitation reuses current chunk",
)

data = read(server_level)
needle = """    public void tickPrecipitation(final BlockPos pos) {
        BlockPos topPos = this.getHeightmapPos(Heightmap.Types.MOTION_BLOCKING, pos);
        BlockPos belowPos = topPos.below();
        Biome biome = this.getBiome(topPos).value();"""
replacement = """    public void tickPrecipitation(final BlockPos pos) {
        this.tickPrecipitation(pos, null);
    }

    // Warext - ported from Leaf PR #854, original patch by HaHaWTH
    public void tickPrecipitation(final BlockPos pos, final @Nullable LevelChunk chunk) {
        BlockPos topPos = chunk == null
            ? this.getHeightmapPos(Heightmap.Types.MOTION_BLOCKING, pos)
            : new BlockPos(pos.getX(), chunk.getHeight(Heightmap.Types.MOTION_BLOCKING, pos.getX(), pos.getZ()) + 1, pos.getZ());
        BlockPos belowPos = topPos.below();
        Biome biome = this.getBiomeCached(chunk, topPos).value();"""
if data.count(needle) != 1:
    raise RuntimeError(f"ServerLevel tickPrecipitation body: expected 1 match, got {data.count(needle)}")
data = data.replace(needle, replacement, 1)
write(server_level, data)
print("[ok] precipitation heightmap/biome lookup reuse")

random_tick = "leaf-server/src/main/java/org/dreeam/leaf/world/RandomTickSystem.java"
replace_once(
    random_tick,
    """                world.tickPrecipitation(world.getBlockRandomPos(pos.getMinBlockX(), 0, pos.getMinBlockZ(), 15));""",
    """                world.tickPrecipitation(world.getBlockRandomPos(pos.getMinBlockX(), 0, pos.getMinBlockZ(), 15), chunk); // Warext - Leaf PR #854""",
    "random tick precipitation chunk reuse",
)

print("Stage 13: precipitation random-tick lookup optimization applied.")


# 26) Async path behavior lifecycle correctness:
#     release stale pending paths when behavior memories invalidate, avoid immediately scheduling
#     another POI path after consuming a finished async result, and stop waiting on stale walk targets.
def insert_before_return_after(rel, anchor, statements, label):
    data = read(rel)
    anchor_pos = data.find(anchor)
    if anchor_pos < 0:
        raise RuntimeError(f"{label}: anchor not found in {rel}")
    return_pos = data.find("return false;", anchor_pos)
    if return_pos < 0:
        raise RuntimeError(f"{label}: return false not found in {rel}")
    line_start = data.rfind("\n", 0, return_pos) + 1
    indent = data[line_start:return_pos]
    insertion = "".join(statement + "\n" + indent for statement in statements)
    data = data[:return_pos] + insertion + data[return_pos:]
    write(rel, data)
    print(f"[ok] {label}")

def insert_after_statement_between(rel, anchor, end_anchor, statement, addition, label):
    data = read(rel)
    start = data.find(anchor)
    if start < 0:
        raise RuntimeError(f"{label}: anchor not found in {rel}")
    end = data.find(end_anchor, start)
    if end < 0:
        raise RuntimeError(f"{label}: end anchor not found in {rel}")
    segment = data[start:end]
    stmt = segment.find(statement)
    if stmt < 0:
        raise RuntimeError(f"{label}: statement not found in {rel}")
    stmt_end = stmt + len(statement)
    absolute_stmt = start + stmt
    line_start = data.rfind("\n", 0, absolute_stmt) + 1
    indent = data[line_start:absolute_stmt]
    segment = segment[:stmt_end] + "\n" + indent + addition + segment[stmt_end:]
    data = data[:start] + segment + data[end:]
    write(rel, data)
    print(f"[ok] {label}")

acquire_poi = "leaf-server/src/minecraft/java/net/minecraft/world/entity/ai/behavior/AcquirePoi.java"

insert_before_return_after(
    acquire_poi,
    "if (!body.getBrain().checkMemory(memoryToValidate, net.minecraft.world.entity.ai.memory.MemoryStatus.VALUE_ABSENT)) {",
    ["pending = null;", "stateSet = null;"],
    "async POI clears stale pending path on validation-memory change",
)

insert_before_return_after(
    acquire_poi,
    "if (!body.getBrain().checkMemory(memoryToAcquire, net.minecraft.world.entity.ai.memory.MemoryStatus.VALUE_ABSENT)) {",
    ["pending = null;", "stateSet = null;"],
    "async POI clears stale pending path on acquisition-memory change",
)

insert_after_statement_between(
    acquire_poi,
    "if (pending != null && stateSet != null) {",
    "if (onlyIfAdult && body.isBaby()) {",
    "stateSet = null;",
    "return true; // Warext - do not schedule a second POI path in the same behavior trigger",
    "async POI avoids duplicate same-tick reschedule",
)

insert_before_return_after(
    acquire_poi,
    "if (onlyIfAdult && body.isBaby()) {",
    ["pending = null;", "stateSet = null;"],
    "async POI clears pending path when adult-only behavior becomes invalid",
)

home = "leaf-server/src/minecraft/java/net/minecraft/world/entity/ai/behavior/SetClosestHomeAsWalkTarget.java"

insert_before_return_after(
    home,
    "if (!body.getBrain().checkMemory(MemoryModuleType.WALK_TARGET, net.minecraft.world.entity.ai.memory.MemoryStatus.VALUE_ABSENT)) {",
    ["pending = null;"],
    "async HOME clears stale pending path when walk target appears",
)

insert_before_return_after(
    home,
    "if (!body.getBrain().checkMemory(MemoryModuleType.HOME, net.minecraft.world.entity.ai.memory.MemoryStatus.VALUE_ABSENT)) {",
    ["pending = null;"],
    "async HOME clears stale pending path when HOME memory appears",
)

move_sink = "leaf-server/src/minecraft/java/net/minecraft/world/entity/ai/behavior/MoveToTargetSink.java"
data = read(move_sink)
old_guard = "if (org.dreeam.leaf.config.modules.async.AsyncPathfinding.enabled && !this.finishedProcessing) return true; // Kaiiju - petal - async path processing - wait for processing"
if data.count(old_guard) != 1:
    raise RuntimeError(f"async MoveToTargetSink stale-target guard: expected 1 match, got {data.count(old_guard)}")
new_guard = """if (org.dreeam.leaf.config.modules.async.AsyncPathfinding.enabled && !this.finishedProcessing) {
            Optional<WalkTarget> pendingTarget = body.getBrain().getMemory(MemoryModuleType.WALK_TARGET);
            return pendingTarget.isPresent()
                && !this.reachedTarget(body, pendingTarget.get())
                && !isWalkTargetSpectator(pendingTarget.get());
        } // Warext - do not keep a stale async navigation behavior alive"""
data = data.replace(old_guard, new_guard, 1)
write(move_sink, data)
print("[ok] async MoveToTargetSink stale-target guard")

print("Stage 14: async path behavior lifecycle cleanup applied.")


# 27) Cache Entity#getEncodeId(): entity type is immutable for an entity's lifetime, so repeated
#     registry/string construction is unnecessary. Null remains uncached for non-serializable entities.
entity_java = "leaf-server/src/minecraft/java/net/minecraft/world/entity/Entity.java"

replace_once(
    entity_java,
    """    private final boolean shouldSkipBaseDespawnCheck = this instanceof net.minecraft.world.entity.projectile.ThrowableProjectile; // Leaf - Rewrite entity despawn time""",
    """    private final boolean shouldSkipBaseDespawnCheck = this instanceof net.minecraft.world.entity.projectile.ThrowableProjectile; // Leaf - Rewrite entity despawn time
    private @Nullable String warext$cachedEncodeId;""",
    "entity encode-id cache field",
)

data = read(entity_java)
needle = """    public final @Nullable String getEncodeId() {
        // Paper start - Raw entity serialization API
        return this.getEncodeId(false);
    }"""
replacement = """    public final @Nullable String getEncodeId() {
        String cached = this.warext$cachedEncodeId;
        if (cached != null) {
            return cached;
        }
        // Paper start - Raw entity serialization API
        cached = this.getEncodeId(false);
        if (cached != null) {
            this.warext$cachedEncodeId = cached;
        }
        return cached;
    }"""
if data.count(needle) != 1:
    raise RuntimeError(f"Entity#getEncodeId cache target: expected 1 match, got {data.count(needle)}")
data = data.replace(needle, replacement, 1)
write(entity_java, data)
print("[ok] cached entity encode id")

print("Stage 15: immutable entity encode-id caching applied.")


# 28) Async pathfinding burst memory/latency control.
# Evaluators are checked out before tasks enter the executor queue, so an oversized queue can
# temporarily create hundreds/thousands of heavyweight evaluator instances. Keep enough backlog
# for throughput without retaining huge stale bursts.
replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/async/AsyncPathfinding.java",
    """        if (asyncPathfindingQueueSize <= 0) {
            asyncPathfindingQueueSize = asyncPathfindingMaxThreads * 256;
        }""",
    """        if (asyncPathfindingQueueSize <= 0) {
            if (availableProcessors <= 4) {
                asyncPathfindingQueueSize = Math.max(
                    128,
                    Math.min(256, asyncPathfindingMaxThreads * 128)
                );
            } else {
                asyncPathfindingQueueSize = Math.max(
                    512,
                    Math.min(1024, asyncPathfindingMaxThreads * 128)
                );
            }
        }""",
    "bounded adaptive pathfinding queue",
)

node_cache = "leaf-server/src/main/java/org/dreeam/leaf/async/path/NodeEvaluatorCache.java"
replace_once(
    node_cache,
    """    private static final ConcurrentHashMap<PoolKey, ConcurrentLinkedQueue<NodeEvaluator>> NODE_EVALUATORS = new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<IdentityKey<NodeEvaluator>, ConcurrentLinkedQueue<NodeEvaluator>> NODE_EVALUATOR_TO_POOL = new ConcurrentHashMap<>();""",
    """    private static final ConcurrentHashMap<PoolKey, ConcurrentLinkedQueue<NodeEvaluator>> NODE_EVALUATORS = new ConcurrentHashMap<>();
    private static final ConcurrentHashMap<IdentityKey<NodeEvaluator>, ConcurrentLinkedQueue<NodeEvaluator>> NODE_EVALUATOR_TO_POOL = new ConcurrentHashMap<>();

    private static int retainedPoolLimit() {
        final int configured = Integer.getInteger("warext.pathfinding.evaluator-pool-cap", 0);
        if (configured > 0) {
            return Math.max(1, Math.min(128, configured));
        }
        final int threads = Math.max(1, org.dreeam.leaf.config.modules.async.AsyncPathfinding.asyncPathfindingMaxThreads);
        return Math.max(4, Math.min(32, threads * 2));
    }""",
    "pathfinding evaluator retained-pool cap",
)

replace_once(
    node_cache,
    """        Validate.notNull(pool, "NodeEvaluator already returned");
        pool.offer(nodeEvaluator);""",
    """        Validate.notNull(pool, "NodeEvaluator already returned");
        if (pool.size() < retainedPoolLimit()) {
            pool.offer(nodeEvaluator);
        }""",
    "discard excess burst evaluators",
)

print("Stage 16: pathfinding burst queue and evaluator retention limits applied.")


# 29) Async tracker: reuse Future[] and join-context arrays across ticks.
# Task objects themselves remain per-tick, but these two wrapper arrays no longer churn when
# the task count is stable (which is the normal case under a steady entity count).
async_tracker = "leaf-server/src/main/java/org/dreeam/leaf/async/tracker/AsyncTracker.java"

replace_once(
    async_tracker,
    """    private Future<TrackerCtx> @Nullable [] fut;""",
    """    @SuppressWarnings("unchecked")
    private Future<TrackerCtx>[] futureBuffer = new Future[0];
    private TrackerCtx[] joinBuffer = new TrackerCtx[0];
    private int futureCount;
    private boolean futuresPending;""",
    "tracker reusable future/join buffers fields",
)

replace_once(
    async_tracker,
    """        @SuppressWarnings("unchecked")
        Future<TrackerCtx>[] futures = new Future[taskCount];

        int cursor = 0;""",
    """        if (this.futureBuffer.length != taskCount) {
            @SuppressWarnings("unchecked")
            Future<TrackerCtx>[] resized = new Future[taskCount];
            this.futureBuffer = resized;
        }
        final Future<TrackerCtx>[] futures = this.futureBuffer;
        this.futureCount = taskCount;
        this.futuresPending = true;

        int cursor = 0;""",
    "tracker Future array reuse",
)

replace_once(
    async_tracker,
    """        exec.unpark();
        this.fut = futures;""",
    """        exec.unpark();""",
    "tracker remove transient Future array assignment",
)

replace_once(
    async_tracker,
    """    public void onEntitiesTickEnd() {
        Future<TrackerCtx>[] task = this.fut;
        if (task == null) {
            return;
        }
        for (final Future<TrackerCtx> fut : task) {
            if (!fut.isDone()) {
                return;
            }
        }
        this.fut = null;
        handle(task);
    }

    public void onTickEnd() {
        Future<TrackerCtx>[] task = this.fut;
        this.fut = null;
        if (task == null) {
            return;
        }
        handle(task);
    }""",
    """    public void onEntitiesTickEnd() {
        if (!this.futuresPending) {
            return;
        }
        final Future<TrackerCtx>[] task = this.futureBuffer;
        final int count = this.futureCount;
        for (int i = 0; i < count; i++) {
            if (!task[i].isDone()) {
                return;
            }
        }
        this.futuresPending = false;
        handle(task, count);
    }

    public void onTickEnd() {
        if (!this.futuresPending) {
            return;
        }
        this.futuresPending = false;
        handle(this.futureBuffer, this.futureCount);
    }""",
    "tracker active future count handling",
)

replace_once(
    async_tracker,
    """    private void handle(final Future<TrackerCtx>[] futures) {
        try {
            TrackerCtx ctx = futures[0].get();
            TrackerCtx[] contexts = new TrackerCtx[futures.length - 1];
            for (int i = 1; i < futures.length; i++) {
                contexts[i - 1] = ctx.join(futures[i].get());
            }
            ctx.handle(contexts);

            for (Future<TrackerCtx> future : futures) {
                TrackerCtx completed = future.get();
                completed.reset();
                this.trackerCtxPool.addLast(completed);
            }
        } catch (final InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (final ExecutionException e) {
            throw new RuntimeException(e);
        }
    }""",
    """    private void handle(final Future<TrackerCtx>[] futures, final int count) {
        try {
            TrackerCtx ctx = futures[0].get();
            final int joinedCount = count - 1;
            if (this.joinBuffer.length != joinedCount) {
                this.joinBuffer = new TrackerCtx[joinedCount];
            }
            final TrackerCtx[] contexts = this.joinBuffer;
            for (int i = 1; i < count; i++) {
                contexts[i - 1] = ctx.join(futures[i].get());
            }
            ctx.handle(contexts);

            for (int i = 0; i < count; i++) {
                TrackerCtx completed = futures[i].get();
                futures[i] = null;
                completed.reset();
                this.trackerCtxPool.addLast(completed);
            }
            java.util.Arrays.fill(contexts, null);
        } catch (final InterruptedException e) {
            Thread.currentThread().interrupt();
        } catch (final ExecutionException e) {
            throw new RuntimeException(e);
        }
    }""",
    "tracker join/future wrapper array reuse",
)

print("Stage 17: reusable tracker Future/join arrays applied.")


# 30) Light packet ThreadLocal retention cleanup.
# The reusable ThreadLocal buffers must not retain DataLayer byte[] references after the packet
# clones them, otherwise a long-lived Netty/server thread can keep chunk-light arrays alive.
light_packet = "leaf-server/src/minecraft/java/net/minecraft/network/protocol/game/ClientboundLightUpdatePacketData.java"
replace_once(
    light_packet,
    """        for (int i = 0; i < skyCount; i++) {
            skyUpdates[i] = skyBuffer[i].clone();
        }""",
    """        for (int i = 0; i < skyCount; i++) {
            skyUpdates[i] = skyBuffer[i].clone();
            skyBuffer[i] = null;
        }""",
    "release sky light ThreadLocal buffer refs",
)
replace_once(
    light_packet,
    """        for (int i = 0; i < blockCount; i++) {
            blockUpdates[i] = blockBuffer[i].clone();
        }""",
    """        for (int i = 0; i < blockCount; i++) {
            blockUpdates[i] = blockBuffer[i].clone();
            blockBuffer[i] = null;
        }""",
    "release block light ThreadLocal buffer refs",
)
print("Stage 18: light packet ThreadLocal retention cleanup applied.")

# 31) Final performance profile layer.
# Balanced is the default for new configurations; extreme opts into the async entity tracker.
profile_java = """package org.dreeam.leaf.performance;

import java.util.Locale;

public final class WarextPerformanceProfile {

    public enum Mode {
        COMPATIBILITY,
        BALANCED,
        EXTREME
    }

    private static final Mode MODE = parse(System.getProperty("warext.profile", "balanced"));

    private WarextPerformanceProfile() {
    }

    private static Mode parse(final String value) {
        if (value == null) {
            return Mode.BALANCED;
        }
        return switch (value.trim().toLowerCase(Locale.ROOT)) {
            case "compat", "compatibility", "safe" -> Mode.COMPATIBILITY;
            case "extreme", "max", "maximum" -> Mode.EXTREME;
            default -> Mode.BALANCED;
        };
    }

    public static Mode mode() {
        return MODE;
    }

    public static boolean isExtreme() {
        return MODE == Mode.EXTREME;
    }

    public static boolean dynamicBrainDefault() {
        return MODE != Mode.COMPATIBILITY;
    }

    public static boolean entityActivationOptimizerDefault() {
        return MODE != Mode.COMPATIBILITY;
    }

    public static boolean asyncPathfindingDefault() {
        return MODE != Mode.COMPATIBILITY && Runtime.getRuntime().availableProcessors() >= 4;
    }

    public static boolean asyncTrackerDefault() {
        return MODE == Mode.EXTREME && Runtime.getRuntime().availableProcessors() >= 4;
    }

    public static int trackerMinEntitiesPerTask() {
        return MODE == Mode.EXTREME ? 32 : 64;
    }
}
"""
profile_path = root / "leaf-server/src/main/java/org/dreeam/leaf/performance/WarextPerformanceProfile.java"
profile_path.parent.mkdir(parents=True, exist_ok=True)
profile_path.write_text(profile_java, encoding="utf-8")
print("[ok] Warext performance profiles")

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/async/AsyncPathfinding.java",
    "    public static boolean enabled = false;",
    "    public static boolean enabled = org.dreeam.leaf.performance.WarextPerformanceProfile.asyncPathfindingDefault();",
    "balanced async pathfinding default",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/async/MultithreadedTracker.java",
    "    public static boolean enabled = false;",
    "    public static boolean enabled = org.dreeam.leaf.performance.WarextPerformanceProfile.asyncTrackerDefault();",
    "extreme async tracker default",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/async/MultithreadedTracker.java",
    '        enabled = globalConfig.getBoolean(basePath() + ".enabled", false);',
    '        enabled = globalConfig.getBoolean(basePath() + ".enabled", enabled);',
    "tracker profile-aware config default",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/async/MultithreadedTracker.java",
    """        threads = globalConfig.getInt(basePath() + ".threads", 0);
        if (threads <= 0) {""",
    """        threads = globalConfig.getInt(basePath() + ".threads", 0);
        minEntitiesPerTask = Math.max(16, globalConfig.getInt(
            basePath() + ".min-entities-per-task",
            org.dreeam.leaf.performance.WarextPerformanceProfile.trackerMinEntitiesPerTask()
        ));
        if (threads <= 0) {""",
    "tracker adaptive task-size config",
)

# Extreme mode gives one additional CPU to async work on sufficiently large hosts while
# still reserving at least one processor for the tick thread / GC / Netty.
budget_file = "leaf-server/src/main/java/org/dreeam/leaf/performance/WarextCpuBudget.java"
replace_once(
    budget_file,
    """        if (PROCESSORS >= 24) return 5;
        if (PROCESSORS >= 16) return 4;
        if (PROCESSORS >= 8) return 2;
        return 1;""",
    """        int reserved;
        if (PROCESSORS >= 24) reserved = 5;
        else if (PROCESSORS >= 16) reserved = 4;
        else if (PROCESSORS >= 8) reserved = 2;
        else reserved = 1;

        if (WarextPerformanceProfile.isExtreme() && reserved > 1) {
            reserved--;
        }
        return reserved;""",
    "profile-aware CPU headroom",
)

replace_once(
    budget_file,
    """        final int budget = workerBudget();
        return Math.max(1, Math.min(8, (budget * 35 + 99) / 100));""",
    """        if (!WarextPerformanceProfile.isExtreme() && PROCESSORS <= 4) {
            // AsyncPathProcessor keeps only one core worker alive in this case; the second
            // thread is burst capacity used only after the bounded queue fills.
            return Math.min(2, PROCESSORS);
        }
        final int budget = workerBudget();
        final int percent = WarextPerformanceProfile.isExtreme() ? 45 : 35;
        return Math.max(1, Math.min(8, (budget * percent + 99) / 100));""",
    "profile-aware pathfinding CPU share",
)

replace_once(
    budget_file,
    """        int path = pathfindingThreads();
        int tracker = Math.max(1, Math.min(8, (budget * 35 + 99) / 100));""",
    """        int path = pathfindingThreads();
        final int percent = WarextPerformanceProfile.isExtreme() ? 45 : 35;
        int tracker = Math.max(1, Math.min(8, (budget * percent + 99) / 100));""",
    "profile-aware tracker CPU share",
)


replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/opt/SleepingBlockEntity.java",
    "    public static boolean enabled = false;",
    "    public static boolean enabled = org.dreeam.leaf.performance.WarextPerformanceProfile.isExtreme();",
    "extreme sleeping block entities default",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/opt/DynamicActivationofBrain.java",
    "    public static boolean enabled = false;",
    "    public static boolean enabled = org.dreeam.leaf.performance.WarextPerformanceProfile.dynamicBrainDefault();",
    "balanced/extreme dynamic brain activation default",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/opt/OptimizeEntityActivation.java",
    "    public static boolean enabled = false;",
    "    public static boolean enabled = org.dreeam.leaf.performance.WarextPerformanceProfile.entityActivationOptimizerDefault();",
    "balanced/extreme entity activation optimizer default",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/config/modules/opt/OptimizeRandomTick.java",
    "    public static boolean enabled = false;",
    "    public static boolean enabled = org.dreeam.leaf.performance.WarextPerformanceProfile.isExtreme();",
    "extreme random tick optimizer default",
)

print("Finalization: Warext balanced/extreme performance profiles applied.")


# 32) VoxelBench baseline tuning (vxb_ho3bolny).
# The baseline exposed three software-side tail-latency issues: redstone, entity AI on a
# 4-vCPU host, and repeated growth allocations while constructing chunk packets. Keep these
# changes workload-independent and avoid benchmark-only mob-count / activation-range nerfs.

# Prefer Alternate Current for new worlds/configurations. Existing explicit config values remain
# authoritative, so upgrading a server does not silently rewrite an administrator's choice.
world_config_candidates = [
    "paper-server/src/main/java/io/papermc/paper/configuration/WorldConfiguration.java",
    "leaf-server/src/main/java/io/papermc/paper/configuration/WorldConfiguration.java",
]
world_config = next((candidate for candidate in world_config_candidates if (root / candidate).exists()), None)
if world_config is None:
    raise RuntimeError("Generated Paper WorldConfiguration.java was not found")
replace_once(
    world_config,
    "        public RedstoneImplementation redstoneImplementation = RedstoneImplementation.VANILLA;",
    "        public RedstoneImplementation redstoneImplementation = RedstoneImplementation.ALTERNATE_CURRENT;",
    "Warext default Alternate Current redstone implementation",
)

# Pre-size chunk block-entity metadata instead of repeatedly growing the backing Object[] during
# chunk/NBT packet construction. This is deliberately bounded to the normal packet-side limit.
chunk_packet = "leaf-server/src/minecraft/java/net/minecraft/network/protocol/game/ClientboundLevelChunkPacketData.java"
replace_once(
    chunk_packet,
    """        this.blockEntitiesData = Lists.newArrayList();
        int totalTileEntities = 0; // Paper - Handle oversized block entities in chunks

        for (final BlockEntity blockEntity : levelChunk.getBlockEntities().values()) { // Leaf - Optimize chunk packet construction""",
    """        final java.util.Map<BlockPos, BlockEntity> warextBlockEntities = levelChunk.getBlockEntities();
        this.blockEntitiesData = new java.util.ArrayList<>(Math.min(warextBlockEntities.size(), BLOCK_ENTITY_LIMIT));
        int totalTileEntities = 0; // Paper - Handle oversized block entities in chunks

        for (final BlockEntity blockEntity : warextBlockEntities.values()) { // Leaf - Optimize chunk packet construction // Warext - reuse lookup + pre-size metadata""",
    "chunk packet block-entity metadata pre-sizing",
)

print("Stage 19: VoxelBench baseline tail-latency tuning applied.")


# 33) NBT network allocation: avoid constructing a ByteBufOutputStream wrapper for every NBT
# write. VoxelBench serializes tens of thousands of objects in this workload, so even a small
# per-object wrapper becomes measurable allocation/GC pressure. The adapter is per-thread and
# clears its ByteBuf reference in finally to avoid retaining packet buffers.
nbt_data_output = """package org.dreeam.leaf.performance;

import io.netty.buffer.ByteBuf;
import net.minecraft.nbt.NbtIo;
import net.minecraft.nbt.Tag;

import java.io.DataOutput;
import java.io.IOException;
import java.io.UTFDataFormatException;

public final class WarextNbtDataOutput implements DataOutput {

    private static final ThreadLocal<WarextNbtDataOutput> LOCAL =
        ThreadLocal.withInitial(WarextNbtDataOutput::new);
    private static final int MAX_RETAINED_UTF_BUFFER = 8192;
    private static final ThreadLocal<byte[]> UTF_BUFFER =
        ThreadLocal.withInitial(() -> new byte[256]);

    private ByteBuf output;

    private WarextNbtDataOutput() {
    }

    public static void writeAnyTag(final Tag tag, final ByteBuf output) throws IOException {
        final WarextNbtDataOutput dataOutput = LOCAL.get();
        dataOutput.output = output;
        try {
            NbtIo.writeAnyTag(tag, dataOutput);
        } finally {
            dataOutput.output = null;
        }
    }

    private ByteBuf output() {
        final ByteBuf current = this.output;
        if (current == null) {
            throw new IllegalStateException("Warext NBT output used outside an active write");
        }
        return current;
    }

    private static byte[] acquireUtfBuffer(final int length) {
        final byte[] current = UTF_BUFFER.get();
        if (current.length >= length) {
            return current;
        }
        if (length > MAX_RETAINED_UTF_BUFFER) {
            return new byte[length];
        }

        int newLength = current.length;
        while (newLength < length) {
            newLength = Math.min(MAX_RETAINED_UTF_BUFFER, newLength << 1);
        }
        final byte[] grown = new byte[newLength];
        UTF_BUFFER.set(grown);
        return grown;
    }

    @Override
    public void write(final int value) {
        output().writeByte(value);
    }

    @Override
    public void write(final byte[] bytes) {
        output().writeBytes(bytes);
    }

    @Override
    public void write(final byte[] bytes, final int offset, final int length) {
        output().writeBytes(bytes, offset, length);
    }

    @Override
    public void writeBoolean(final boolean value) {
        output().writeBoolean(value);
    }

    @Override
    public void writeByte(final int value) {
        output().writeByte(value);
    }

    @Override
    public void writeShort(final int value) {
        output().writeShort(value);
    }

    @Override
    public void writeChar(final int value) {
        output().writeChar(value);
    }

    @Override
    public void writeInt(final int value) {
        output().writeInt(value);
    }

    @Override
    public void writeLong(final long value) {
        output().writeLong(value);
    }

    @Override
    public void writeFloat(final float value) {
        output().writeFloat(value);
    }

    @Override
    public void writeDouble(final double value) {
        output().writeDouble(value);
    }

    @Override
    public void writeBytes(final String value) {
        final ByteBuf buffer = output();
        for (int i = 0, length = value.length(); i < length; i++) {
            buffer.writeByte(value.charAt(i));
        }
    }

    @Override
    public void writeChars(final String value) {
        final ByteBuf buffer = output();
        for (int i = 0, length = value.length(); i < length; i++) {
            buffer.writeChar(value.charAt(i));
        }
    }

    @Override
    public void writeUTF(final String value) throws IOException {
        final int charLength = value.length();
        int utfLength = 0;
        boolean ascii = true;

        for (int i = 0; i < charLength; i++) {
            final int c = value.charAt(i);
            if (c >= 0x0001 && c <= 0x007F) {
                utfLength++;
            } else {
                ascii = false;
                if (c > 0x07FF) {
                    utfLength += 3;
                } else {
                    utfLength += 2;
                }
            }
        }

        if (utfLength > 65535) {
            throw new UTFDataFormatException("encoded string too long: " + utfLength + " bytes");
        }

        final ByteBuf buffer = output();
        buffer.writeShort(utfLength);

        // NBT field names and most protocol strings are overwhelmingly 7-bit ASCII.
        // Netty's bulk ASCII writer avoids one virtual writeByte call per character.
        if (ascii) {
            io.netty.buffer.ByteBufUtil.writeAscii(buffer, value);
            return;
        }

        final byte[] encoded = acquireUtfBuffer(utfLength);
        int out = 0;
        for (int i = 0; i < charLength; i++) {
            final int c = value.charAt(i);
            if (c >= 0x0001 && c <= 0x007F) {
                encoded[out++] = (byte) c;
            } else if (c > 0x07FF) {
                encoded[out++] = (byte) (0xE0 | ((c >> 12) & 0x0F));
                encoded[out++] = (byte) (0x80 | ((c >> 6) & 0x3F));
                encoded[out++] = (byte) (0x80 | (c & 0x3F));
            } else {
                encoded[out++] = (byte) (0xC0 | ((c >> 6) & 0x1F));
                encoded[out++] = (byte) (0x80 | (c & 0x3F));
            }
        }
        buffer.writeBytes(encoded, 0, utfLength);
    }
}
"""
write("leaf-server/src/main/java/org/dreeam/leaf/performance/WarextNbtDataOutput.java", nbt_data_output)
print("[ok] reusable NBT ByteBuf DataOutput adapter")

replace_once(
    "leaf-server/src/minecraft/java/net/minecraft/network/FriendlyByteBuf.java",
    "            NbtIo.writeAnyTag(tag, new ByteBufOutputStream(output));",
    "            org.dreeam.leaf.performance.WarextNbtDataOutput.writeAnyTag(tag, output);",
    "NBT ByteBufOutputStream allocation removal",
)

print("Stage 20: NBT network serialization allocation reduction applied.")


# 34) VoxelBench vxb_tbaakena AI allocation pass.
# Leaf already randomizes Sensor start phases through Brain#randomlyDelayStart, so do not add
# duplicate staggering. Instead retain the large temporary entity/player arrays across scans.
# Memory-facing lists use ping-pong buffers: the buffer still referenced by Brain memory is never
# cleared while building the next scan result.

# Memory-facing NearestLivingEntitySensor/PlayerSensor result lists intentionally remain
# freshly allocated. Those lists are published into Brain memory and may outlive the next scan;
# reusing/clearing them could mutate a previously observed memory value and change AI semantics.

nearest_item_sensor = "leaf-server/src/minecraft/java/net/minecraft/world/entity/ai/sensing/NearestItemSensor.java"
replace_once(
    nearest_item_sensor,
    "public class NearestItemSensor extends Sensor<Mob> {",
    """public class NearestItemSensor extends Sensor<Mob> {
    private final it.unimi.dsi.fastutil.objects.ObjectArrayList<ItemEntity> warextItems =
        new it.unimi.dsi.fastutil.objects.ObjectArrayList<>(16);""",
    "nearest-item sensor reusable scratch field",
)
replace_once(
    nearest_item_sensor,
    "        it.unimi.dsi.fastutil.objects.ObjectArrayList<ItemEntity> items = new it.unimi.dsi.fastutil.objects.ObjectArrayList<>();",
    """        final it.unimi.dsi.fastutil.objects.ObjectArrayList<ItemEntity> items = this.warextItems;
        items.clear();""",
    "nearest-item sensor scratch reuse",
)

print("Stage 21: AI sensor hot-path allocation reuse applied.")


# 35) Product-facing Warext Server Engine branding.
# Keep upstream package/class names where compatibility requires them, but do not expose the
# upstream project name as the product identity in runtime brand, commands, config headers,
# replay metadata, manifests, or user-facing diagnostics.
server_brand = "leaf-server/src/main/java/org/dreeam/leaf/config/modules/misc/ServerBrand.java"
replace_once(
    server_brand,
    """    public static String serverModName = io.papermc.paper.ServerBuildInfo.buildInfo().brandName();
    public static String serverGUIName = io.papermc.paper.ServerBuildInfo.buildInfo().brandName() + " Console";""",
    """    public static String serverModName = "Warext Server Engine";
    public static String serverGUIName = "Warext Server Engine Console";""",
    "Warext runtime server brand defaults",
)
replace_once(
    server_brand,
    """        serverModName = globalConfig.getString(basePath() + ".server-mod-name", serverModName);
        serverGUIName = globalConfig.getString(basePath() + ".server-gui-name", serverGUIName);""",
    """        serverModName = globalConfig.getString(basePath() + ".server-mod-name", serverModName);
        serverGUIName = globalConfig.getString(basePath() + ".server-gui-name", serverGUIName);
        if ("Leaf".equalsIgnoreCase(serverModName)) {
            serverModName = "Warext Server Engine";
        }
        if ("Leaf Console".equalsIgnoreCase(serverGUIName)) {
            serverGUIName = "Warext Server Engine Console";
        }""",
    "migrate legacy runtime brand values",
)

leaf_command = "leaf-server/src/main/java/org/dreeam/leaf/command/LeafCommand.java"
replace_once(
    leaf_command,
    '    public static final String COMMAND_LABEL = "leaf";',
    '    public static final String COMMAND_LABEL = "warext";',
    "Warext command label",
)
replace_once(
    leaf_command,
    '    public static final String BASE_PERM = LeafCommands.COMMAND_BASE_PERM + "." + COMMAND_LABEL;',
    '    public static final String BASE_PERM = LeafCommands.COMMAND_BASE_PERM;',
    "Warext command permission root",
)
replace_once(
    leaf_command,
    '        this.description = "Leaf related commands";',
    '        this.description = "Warext Server Engine commands";',
    "Warext command description",
)

leaf_commands = "leaf-server/src/main/java/org/dreeam/leaf/command/LeafCommands.java"
replace_once(
    leaf_commands,
    '    public static final String COMMAND_BASE_PERM = CraftDefaultPermissions.LEAF_ROOT + ".command";',
    '    public static final String COMMAND_BASE_PERM = "warext.command";',
    "Warext command permission namespace",
)
replace_once(
    leaf_commands,
    '        COMMANDS.forEach((s, command) -> server.server.getCommandMap().register(s, "Leaf", command));',
    '        COMMANDS.forEach((s, command) -> server.server.getCommandMap().register(s, "Warext", command));',
    "Warext command fallback namespace",
)

replace_once(
    "leaf-server/src/main/java/org/dreeam/leaf/command/subcommands/MSPTCommand.java",
    '                .content("Please enable it in your Leaf configuration to use this command.")',
    '                .content("Please enable it in your Warext Server Engine configuration to use this command.")',
    "Warext MSPT configuration message",
)

leaf_config = "leaf-server/src/main/java/org/dreeam/leaf/config/LeafConfig.java"
replace_once(
    leaf_config,
    '    protected static final String GLOBAL_CONFIG_FILE = "leaf-global.yml";',
    '    protected static final String GLOBAL_CONFIG_FILE = "warext-global.yml";',
    "Warext global config filename",
)
replace_once(
    leaf_config,
    '    protected static final String DEFAULT_WORLD_CONFIG_FILE = "leaf-world-defaults.yml"; // Leaf TODO - Per world config',
    '    protected static final String DEFAULT_WORLD_CONFIG_FILE = "warext-world-defaults.yml"; // upstream TODO - Per world config',
    "Warext world config filename",
)
replace_once(
    leaf_config,
    '            "config/leaf-global.yml",',
    '            "config/warext-global.yml",',
    "Warext spark config filename",
)
replace_once(
    leaf_config,
    """        globalConfig = new LeafGlobalConfig(init);""",
    """        final Path warextGlobalConfig = CONFIG_DIRECTORY.toPath().resolve(GLOBAL_CONFIG_FILE);
        final Path legacyGlobalConfig = CONFIG_DIRECTORY.toPath().resolve("leaf-global.yml");
        if (!Files.exists(warextGlobalConfig) && Files.exists(legacyGlobalConfig)) {
            Files.move(legacyGlobalConfig, warextGlobalConfig, StandardCopyOption.REPLACE_EXISTING);
            LOGGER.info("Migrated legacy upstream configuration to {}", warextGlobalConfig);
        }

        globalConfig = new LeafGlobalConfig(init);""",
    "migrate legacy global config filename",
)

global_config = "leaf-server/src/main/java/org/dreeam/leaf/config/LeafGlobalConfig.java"
data = read(global_config)
old_comment = """        configFile.addComments("config-version", pickStringRegionBased(\"\"\"
                Leaf Config

                Website: https://www.leafmc.one/
                Docs: https://www.leafmc.one/docs/getting-started
                GitHub Repo: https://github.com/Winds-Studio/Leaf
                Discord: https://discord.com/invite/gfgAwdSEuM\"\"\",
            \"\"\"
                Leaf 配置

                官网: https://www.leafmc.one/zh/
                文档: https://www.leafmc.one/zh/docs/getting-started
                GitHub 仓库: https://github.com/Winds-Studio/Leaf
                QQ社区群: 619278377\"\"\"));"""
new_comment = """        configFile.addComments("config-version", pickStringRegionBased(\"\"\"
                Warext Server Engine Configuration

                Project: https://github.com/benjamin1734/Warext-Studis-Optimized-Minecraft-Fork
                Releases: https://github.com/benjamin1734/Warext-Studis-Optimized-Minecraft-Fork/releases\"\"\",
            \"\"\"
                Warext Server Engine 配置

                项目: https://github.com/benjamin1734/Warext-Studis-Optimized-Minecraft-Fork
                发布: https://github.com/benjamin1734/Warext-Studis-Optimized-Minecraft-Fork/releases\"\"\"));"""
if data.count(old_comment) != 1:
    raise RuntimeError(f"Warext config header: expected 1 match, got {data.count(old_comment)}")
write(global_config, data.replace(old_comment, new_comment, 1))
print("[ok] Warext config header")

version_fetcher = """package org.dreeam.leaf.version;

import net.kyori.adventure.text.Component;
import net.kyori.adventure.text.format.NamedTextColor;
import org.galemc.gale.version.AbstractPaperVersionFetcher;

public class LeafVersionFetcher extends AbstractPaperVersionFetcher {

    public static final String DOWNLOAD_PAGE = "https://github.com/benjamin1734/Warext-Studis-Optimized-Minecraft-Fork/releases";

    public LeafVersionFetcher() {
        super(
            DOWNLOAD_PAGE,
            "Warext Studios",
            "Warext Server Engine",
            "benjamin1734",
            "Warext-Studis-Optimized-Minecraft-Fork",
            ApiType.GITHUB
        );
    }

    @Override
    public Component getVersionMessage() {
        return Component.text("* Warext Server Engine experimental build", NamedTextColor.GREEN)
            .append(Component.newline())
            .append(Component.text("Releases: " + DOWNLOAD_PAGE, NamedTextColor.GOLD));
    }
}
"""
write("leaf-server/src/main/java/org/dreeam/leaf/version/LeafVersionFetcher.java", version_fetcher)
print("[ok] Warext version output")

for replay_file in [
    "leaf-server/src/main/java/org/leavesmc/leaves/replay/RecorderOption.java",
    "leaf-server/src/main/java/org/leavesmc/leaves/replay/RecordMetaData.java",
    "leaf-api/src/main/java/org/leavesmc/leaves/replay/BukkitRecorderOption.java",
]:
    replay_path = root / replay_file
    if not replay_path.exists():
        continue
    data = replay_path.read_text(encoding="utf-8")
    old = 'public String serverName = "Leaf";'
    if old in data:
        replay_path.write_text(data.replace(old, 'public String serverName = "Warext Server Engine";', 1), encoding="utf-8")
        print(f"[ok] Warext replay brand: {replay_file}")

build_file = root / "leaf-server/build.gradle.kts"
if not build_file.exists():
    raise RuntimeError("Generated leaf-server/build.gradle.kts missing after upstream patch application")
data = build_file.read_text(encoding="utf-8")
for old, new, label in [
    ('"Implementation-Title" to "Leaf"', '"Implementation-Title" to "Warext Server Engine"', "manifest implementation title"),
    ('"Specification-Title" to "Leaf"', '"Specification-Title" to "Warext Server Engine"', "manifest specification title"),
    ('"Specification-Vendor" to "Winds Studio"', '"Specification-Vendor" to "Warext Studios"', "manifest vendor"),
    ('"Brand-Id" to "winds-studio:leaf"', '"Brand-Id" to "warext:server-engine"', "manifest brand id"),
    ('"Brand-Name" to "Leaf"', '"Brand-Name" to "Warext Server Engine"', "manifest brand name"),
]:
    if data.count(old) != 1:
        raise RuntimeError(f"{label}: expected 1 match in generated build file, got {data.count(old)}")
    data = data.replace(old, new, 1)
build_file.write_text(data, encoding="utf-8")
print("[ok] Warext JAR manifest branding")

# User-facing diagnostics that survive the upstream patch stack.
optional_replacements = [
    (
        "paper-server/src/main/java/org/spigotmc/WatchdogThread.java",
        "If you think this is a Leaf bug, please report it at https://github.com/Winds-Studio/Leaf/issues",
        "If you think this is a Warext Server Engine issue, please report it at https://github.com/benjamin1734/Warext-Studis-Optimized-Minecraft-Fork/issues",
        "Warext watchdog diagnostics",
    ),
    (
        "leaf-server/src/minecraft/java/net/minecraft/world/level/chunk/storage/RegionFileStorage.java",
        "DO NOT REPORT THIS TO PAPER OR LEAF",
        "DO NOT REPORT THIS TO PAPER OR WAREXT SERVER ENGINE",
        "Warext region diagnostics product name",
    ),
    (
        "leaf-server/src/minecraft/java/net/minecraft/world/level/chunk/storage/RegionFileStorage.java",
        "If you think this is a Leaf bug, please report it at https://github.com/Winds-Studio/Leaf/issues",
        "If you think this is a Warext Server Engine issue, please report it at https://github.com/benjamin1734/Warext-Studis-Optimized-Minecraft-Fork/issues",
        "Warext region diagnostics issue link",
    ),
]
for rel, old, new, label in optional_replacements:
    path = root / rel
    if path.exists():
        data = path.read_text(encoding="utf-8")
        if old in data:
            path.write_text(data.replace(old, new), encoding="utf-8")
            print(f"[ok] {label}")

print("Branding: Warext Server Engine product identity applied.")

print("All Warext Server Engine 26.3 performance patches applied.")
