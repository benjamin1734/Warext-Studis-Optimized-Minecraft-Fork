# Warext Server Engine 26.3 exp.12

Experimental high-performance server engine build for the 26.3 line.

## Async path completion allocation reduction
- Remove the unused per-path post-processing callback list after Warext's one-time navigation prepare/trim lifecycle made it unnecessary.
- Remove callback Consumer registration and callback-array copying during path completion.
- Remove the dedicated completion lock object and publish completion through a single synchronized finalization path.
- Keep `ready` volatile so completed path state is visible across worker/tick threads.
- Remove `AsyncPathProcessor.awaitProcessing()` and its now-unused callback API.
- Preserve idempotent path completion and all existing stale-target, Brain.PATH, fallback, POI and Paper API correctness fixes.

## Existing memory fixes retained
- ThreadLocal sky/block light packet buffers are cleared after cloning, so chunk-light arrays are not retained by long-lived threads.
- Reusable tracker, collision, spawn/despawn, NBT and pathfinding buffers remain enabled.

## Upstream review
- Leaf `ver/26.3` still points to `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.
- PR #940 light-buffer retention was already present in Warext and was not duplicated.
- PR #928 deferred container item decoding was intentionally not included in this release because it changes NBT/Lithium/container lifecycle across multiple classes and should be isolated in a dedicated compatibility release.

## Validation / publishing
- Build the runnable Paperclip server JAR.
- Start and stop real servers with both `balanced` and `extreme` profiles.
- Verify Warext runtime branding/config generation.
- Publish `Warext-Server-Engine-26.3.jar`, `SHA256SUMS.txt`, and `UPSTREAM_COMMIT.txt` to GitHub Releases only after successful validation.
- Fail the workflow if the release is missing the runnable `.jar`.

This release remains a prerelease while the Leaf 26.3 line is still being stabilized.
