# Warext Server Engine 26.3 exp.5

Experimental high-performance server engine build for the 26.3 line.

## Upstream 26.3 refresh
- Rebase the build target onto Leaf 26.3 commit `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.
- Carry the October 5 async-tracker correctness fix and current 26.3 tracker/interpolation model.
- Inherit current 26.3 world-generation cache corrections, zero-movement early-return fix, entity wake-up random change, and Direction#getOpposite regression revert.
- Keep Warext-specific CPU budgeting, bounded async path queues, allocation reductions, and VoxelBench-driven tuning on top.
- Reconcile Warext tracker optimizations with the refreshed async-tracker lifecycle instead of restoring obsolete pre-26.3 assumptions.
- Preserve Warext async mob-spawn stall protection and precipitation lookup optimization while avoiding duplicate upstream logic.

## Validation / publishing
- Build the runnable Paperclip server JAR from the pinned upstream source.
- Start and stop real servers with both `balanced` and `extreme` profiles.
- Verify Warext runtime branding/config generation.
- Publish `Warext-Server-Engine-26.3.jar`, `SHA256SUMS.txt`, and `UPSTREAM_COMMIT.txt` to GitHub Releases only after successful validation.
- Fail the workflow if the versioned release does not contain the runnable `.jar` asset.

## Profiles
- `compatibility`: conservative async defaults.
- `balanced`: default; optimized async pathfinding on suitable CPUs, async tracker off by default.
- `extreme`: enables the optimized async tracker, experimental entity-activation/random-tick/sleeping-block-entity/DAB optimizers, and allocates more async CPU headroom.

Select with `-Dwarext.profile=balanced` or `-Dwarext.profile=extreme`.

This release remains a prerelease while the 26.3 upstream line is still being stabilized.
