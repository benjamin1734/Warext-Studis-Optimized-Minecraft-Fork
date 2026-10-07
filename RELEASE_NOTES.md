# Warext Server Engine 26.3 exp.18

Experimental high-performance server engine build for the 26.3 line.

## Compact uniform chunk-section bit storage
- Add `performance.memory.compact-bit-storage.enabled`.
- Enabled by default.
- When a deserialized chunk section uses more than one palette bit but every stored palette index is zero, collapse it back to the uniform 0-bit representation.
- Preserve the exact logical palette value at index 0.
- Leave malformed or unknown palette data in its original decoded representation instead of forcing compaction.
- Run Paper/Moonrise palette-read bookkeeping after any compaction so existing chunk read optimizations stay correct.
- No biome, block, world-generation or chunk scheduling decisions are changed.

The optimization is independently adapted from the compact-bit-storage idea used by ModernFix and reviewed in Leaf PR #946.

## Why this matters
Empty or single-value chunk sections can otherwise retain unnecessary long-array bit storage after deserialization. On large pregenerated worlds this wastes heap and increases GC pressure even though every cell resolves to the same palette value.

## Validation
- Balanced real-server smoke boot.
- Extreme real-server smoke boot.
- Pregeneration real-server smoke boot.
- Distant End worldgen smoke.
- Normal noise-world Aquifer smoke with fixed seed `8675309`.
- Saved Overworld chunk `[32,32]` is flushed to disk.
- The same world is restarted and the saved chunk is force-loaded again, exercising real `PalettedContainer.read()` deserialization with compact-bit-storage enabled.
- Reload must save and stop without palette, bit-storage, corruption or chunk-load errors.
- Deferred-container chest NBT restart/decode smoke remains mandatory.
- Runnable Paperclip JAR build and GitHub Release JAR verification remain mandatory.

## Existing pregeneration stack retained
- 4-vCPU worker policy.
- Bounded chunk NBT pending-write pressure control.
- XYZ/sampler-safe End biome cache.
- Vanilla-equivalent Aquifer center precompute.
- Deferred container item decoding remains opt-in.

## Upstream base
- Leaf 26.3 remains pinned at `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`; no newer `ver/26.3` commit was available when this release was prepared.

This release remains a prerelease while the Leaf 26.3 line is still being stabilized.
