# Warext Server Engine 26.3 exp.9

Experimental high-performance server engine build for the 26.3 line.

## Async navigation prepare / trim lifecycle
- Carry per-request reach-range metadata with the generated path instead of relying on callback timing.
- Prepare each installed asynchronous path exactly once after the worker has completed it.
- Run navigation `trimPath()` only against a fully processed path so sunlight avoidance and cauldron/path-node corrections are never evaluated against an unfinished route.
- Keep repeated synchronous `moveTo` calls compatible with normal trimming semantics.
- Reset prepared-path state whenever navigation recomputes, replaces, stops, or clears a path.
- Finalize async metadata (target, reach range, stuck timeout) when the installed path becomes ready.
- Apply the same readiness gate to flying navigation.
- Prepare wall-climber paths before their early `isDone()` check.
- Preserve exp.8 Brain.PATH/fallback synchronization and all prior async correctness/performance fixes.

## Upstream base
- Leaf 26.3 pinned at `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.
- No newer `ver/26.3` commit was available when this release was prepared.

## Validation / publishing
- Build the runnable Paperclip server JAR.
- Start and stop real servers with both `balanced` and `extreme` profiles.
- Verify Warext runtime branding/config generation.
- Publish `Warext-Server-Engine-26.3.jar`, `SHA256SUMS.txt`, and `UPSTREAM_COMMIT.txt` to GitHub Releases only after successful validation.
- Fail the workflow if the release is missing the runnable `.jar`.

This release remains a prerelease while the Leaf 26.3 line is still being stabilized.
