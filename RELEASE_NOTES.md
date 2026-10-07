# Warext Server Engine 26.3 exp.11

Experimental high-performance server engine build for the 26.3 line.

## 26.3-native async path correctness
- Preserve mounted-mob pathfinding malus inheritance while keeping amphibious WALKABLE/WATER_BORDER costs local to each evaluator.
- Avoid bypassing passenger/vehicle malus inheritance when an amphibious evaluator supplies its temporary path costs.
- Keep all previous generator-scoped evaluator pooling and async lifecycle protections.

## Entity-distance sorting
- Raise the small-list insertion-sort threshold in `FastBitRadixSort`.
- Skip radix recursion entirely for the common small entity lists.
- Start radix traversal at the highest bit that actually differs across the active list.
- Track differing bits independently for left/right partitions to avoid scanning identical high-order bit levels recursively.
- Keep the existing reusable key buffer and allocation-free sort path.

## Upstream review
- Leaf `ver/26.3` still points to `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.
- Compared Warext against 26.3-native Leaf PR #943; stale-target handling already existed in Warext and was not duplicated.
- This release selectively carries only the non-duplicated, low-risk pieces relevant to correctness and hot-path sorting.

## Validation / publishing
- Build the runnable Paperclip server JAR.
- Start and stop real servers with both `balanced` and `extreme` profiles.
- Verify Warext runtime branding/config generation.
- Publish `Warext-Server-Engine-26.3.jar`, `SHA256SUMS.txt`, and `UPSTREAM_COMMIT.txt` to GitHub Releases only after successful validation.
- Fail the workflow if the release is missing the runnable `.jar`.

This release remains a prerelease while the Leaf 26.3 line is still being stabilized.
