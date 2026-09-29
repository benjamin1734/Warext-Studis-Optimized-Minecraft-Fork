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
                    .setNameFormat("Warext Leaf IO Thread")
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
#     - never force pending POI path calculations onto the server thread
#     - invalidate stale async results when behavior memories change
#     - revalidate POI ownership/type after asynchronous calculation completes
acquire_poi = "leaf-server/src/minecraft/java/net/minecraft/world/entity/ai/behavior/AcquirePoi.java"

replace_once(
    acquire_poi,
    """        if (!body.getBrain().checkMemory(memoryToValidate, net.minecraft.world.entity.ai.memory.MemoryStatus.VALUE_ABSENT)) {
            return false;
        }
        if (memoryToValidate != memoryToAcquire) {
            if (!body.getBrain().checkMemory(memoryToAcquire, net.minecraft.world.entity.ai.memory.MemoryStatus.VALUE_ABSENT)) {
                return false;
            }
        }
        RandomSource random = level.getRandom();
        if (pending != null && stateSet != null) {
            pending.process();
            processPath(poiType, onPoiAcquisitionEvent, batchCache, level, body, memoryToAcquire, timestamp, level.getPoiManager(), stateSet, pending, random);
            pending = null;
            stateSet = null;
        }

        if (onlyIfAdult && body.isBaby()) {
            return false;
        }""",
    """        if (!body.getBrain().checkMemory(memoryToValidate, net.minecraft.world.entity.ai.memory.MemoryStatus.VALUE_ABSENT)) {
            pending = null;
            stateSet = null;
            return false;
        }
        if (memoryToValidate != memoryToAcquire) {
            if (!body.getBrain().checkMemory(memoryToAcquire, net.minecraft.world.entity.ai.memory.MemoryStatus.VALUE_ABSENT)) {
                pending = null;
                stateSet = null;
                return false;
            }
        }

        if (onlyIfAdult && body.isBaby()) {
            pending = null;
            stateSet = null;
            return false;
        }

        RandomSource random = level.getRandom();
        if (pending != null && stateSet != null) {
            // Do not turn queue pressure into an MSPT spike by processing this path synchronously.
            if (!pending.isProcessed()) {
                return false;
            }
            processPath(poiType, onPoiAcquisitionEvent, batchCache, level, body, memoryToAcquire, timestamp, level.getPoiManager(), stateSet, pending, random, validPoi);
            pending = null;
            stateSet = null;
            return true;
        }""",
    "async POI wait without server-thread path execution",
)

# Update the normal completed-path call to carry the validator as well.
data=read(acquire_poi)
old="""            processPath(poiType, onPoiAcquisitionEvent, batchCache, level, body, memoryToAcquire, timestamp, poiManager, poiPositions, path, random);"""
new="""            processPath(poiType, onPoiAcquisitionEvent, batchCache, level, body, memoryToAcquire, timestamp, poiManager, poiPositions, path, random, validPoi);"""
count=data.count(old)
if count != 1:
    raise RuntimeError(f"AcquirePoi direct processPath call: expected 1 match, got {count}")
data=data.replace(old,new,1)
write(acquire_poi,data)
print("[ok] async POI validator propagation")

replace_once(
    acquire_poi,
    """                                    final Set<Pair<Holder<PoiType>, BlockPos>> poiPositions,
                                    final @org.jspecify.annotations.Nullable Path path,
                                    final RandomSource random) {
        if (path != null && path.canReach()) {
            BlockPos targetPos = path.getTarget();
            poiManager.getType(targetPos).ifPresent(type -> {
                poiManager.take(poiType, (t, poiPos) -> poiPos.equals(targetPos), targetPos, 1);
                walkTarget.getBrain().setMemory(memoryToAcquire, GlobalPos.of(level.dimension(), targetPos));
                onPoiAcquisitionEvent.ifPresent(event -> level.broadcastEntityEvent(walkTarget, event));
                batchCache.clear();
                level.debugSynchronizers().updatePoi(targetPos);
            });""",
    """                                    final Set<Pair<Holder<PoiType>, BlockPos>> poiPositions,
                                    final @org.jspecify.annotations.Nullable Path path,
                                    final RandomSource random,
                                    final BiPredicate<ServerLevel, BlockPos> validPoi) {
        if (path != null && path.canReach()) {
            BlockPos targetPos = path.getTarget();
            // World/POI state may have changed while the path was computed off-thread.
            if (!validPoi.test(level, targetPos)) {
                return;
            }
            poiManager.take(poiType, (t, poiPos) -> poiPos.equals(targetPos), targetPos, 1).ifPresent(acquiredPos -> {
                walkTarget.getBrain().setMemory(memoryToAcquire, GlobalPos.of(level.dimension(), acquiredPos));
                onPoiAcquisitionEvent.ifPresent(event -> level.broadcastEntityEvent(walkTarget, event));
                batchCache.clear();
                level.debugSynchronizers().updatePoi(acquiredPos);
            });""",
    "async POI result revalidation",
)

home = "leaf-server/src/minecraft/java/net/minecraft/world/entity/ai/behavior/SetClosestHomeAsWalkTarget.java"
replace_once(
    home,
    """        if (!body.getBrain().checkMemory(MemoryModuleType.WALK_TARGET, net.minecraft.world.entity.ai.memory.MemoryStatus.VALUE_ABSENT)) {
            return false;
        }
        if (!body.getBrain().checkMemory(MemoryModuleType.HOME, net.minecraft.world.entity.ai.memory.MemoryStatus.VALUE_ABSENT)) {
            return false;
        }

        if (pending != null) {
            pending.process();
            processPath(speedModifier, batchCache, lastUpdate, body, level, level.getPoiManager(), stateInt, pending);
            pending = null;
        }""",
    """        if (!body.getBrain().checkMemory(MemoryModuleType.WALK_TARGET, net.minecraft.world.entity.ai.memory.MemoryStatus.VALUE_ABSENT)) {
            pending = null;
            return false;
        }
        if (!body.getBrain().checkMemory(MemoryModuleType.HOME, net.minecraft.world.entity.ai.memory.MemoryStatus.VALUE_ABSENT)) {
            pending = null;
            return false;
        }

        if (pending != null) {
            if (!pending.isProcessed()) {
                return false;
            }
            processPath(speedModifier, batchCache, lastUpdate, body, level, level.getPoiManager(), stateInt, pending);
            pending = null;
            return true;
        }""",
    "async home path wait without synchronous processing",
)

replace_once(
    home,
    """            Optional<Holder<PoiType>> type = poiManager.getType(targetPos);
            if (type.isPresent()) {
                walkTarget.getBrain().setMemory(MemoryModuleType.WALK_TARGET, new WalkTarget(targetPos, speedModifier, 1));
                level.debugSynchronizers().updatePoi(targetPos);
            }""",
    """            Optional<Holder<PoiType>> type = poiManager.getType(targetPos);
            // Revalidate after off-thread calculation: the target may no longer be a HOME POI.
            if (type.isPresent() && type.get().is(PoiTypes.HOME)) {
                walkTarget.getBrain().setMemory(MemoryModuleType.WALK_TARGET, new WalkTarget(targetPos, speedModifier, 1));
                level.debugSynchronizers().updatePoi(targetPos);
            }""",
    "async home POI result revalidation",
)

print("Stage 7: async pathfinding POI correctness/p99 fixes applied.")

print("All Warext optimized Leaf 26.3 performance patches applied.")
