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

print("All Warext optimized Leaf 26.3 performance patches applied.")
