# Warext Server Engine 26.3 exp.6

Experimental high-performance server engine build for the 26.3 line.

## Async pathfinding correctness
- Harden `NearestBedSensor` async path handling based on the relevant Leaf #921 correctness work without importing the incompatible 26.2 patch stack wholesale.
- Never force an unfinished nearest-bed async path to finish on the main tick thread.
- Drop a pending nearest-bed path when the mob is no longer a baby and the result is no longer relevant.
- Keep the sensor scan interval advancing while async work is pending.
- Revalidate that the resolved POI is still a `HOME` before publishing `NEAREST_BED` into Brain memory.
- Retain Warext's generator-scoped lock-free NodeEvaluator pools, nonblocking POI/HOME behavior handling, bounded queues, and 4-vCPU burst protection.

## Upstream base
- Leaf 26.3 pinned at `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.
- Includes the October 5 async tracker fix and the current 26.3 tracker/interpolation model.

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
