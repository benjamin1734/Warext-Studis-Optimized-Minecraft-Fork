# Warext Server Engine 26.3 exp.10

Experimental high-performance server engine build for the 26.3 line.

## Paper pathfinding API compatibility
- Keep Paper's plugin-facing Pathfinder API synchronous even when server pathfinding is asynchronous internally.
- `findPath(Location)` and `findPath(Entity)` now finish an unfinished AsyncPath before returning a `PathResult`.
- `getCurrentPath()` finalizes an unfinished AsyncPath and requires the path to still be the navigation's installed path before exposing it to plugins.
- Finalize Warext navigation preparation before wrapping a current path so plugin callers do not observe a route before async target/reach-range/trim metadata is ready.
- Avoid exposing half-populated async path node lists to plugins.

## Existing async path hardening retained
- One-time async PathNavigation prepare/trim lifecycle.
- Brain.PATH synchronization and fallback-path correctness.
- Partial-path preservation and stale-target handling.
- Nonblocking POI/HOME/nearest-bed handling.
- Amphibious path-cost isolation and generator-scoped evaluator pools.
- Bounded queues, adaptive worker budgeting, and 4-vCPU burst protection.

## Upstream base
- Leaf 26.3 pinned at `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.
- Frog-specific async NodeEvaluator generation is already present in this upstream base and is not duplicated by Warext.

## Validation / publishing
- Build the runnable Paperclip server JAR.
- Start and stop real servers with both `balanced` and `extreme` profiles.
- Verify Warext runtime branding/config generation.
- Publish `Warext-Server-Engine-26.3.jar`, `SHA256SUMS.txt`, and `UPSTREAM_COMMIT.txt` to GitHub Releases only after successful validation.
- Fail the workflow if the release is missing the runnable `.jar`.

This release remains a prerelease while the Leaf 26.3 line is still being stabilized.
