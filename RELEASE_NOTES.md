# Warext Server Engine 26.3 exp.7

Experimental high-performance server engine build for the 26.3 line.

## Amphibious async pathfinding correctness
- Isolate temporary amphibious WALKABLE and WATER_BORDER path costs inside the evaluator instead of mutating shared Mob pathfinding state.
- Route WalkNodeEvaluator malus reads through an overridable helper so pooled async evaluators can provide request-local costs safely.
- Remove the temporary WALKABLE/WATER_BORDER write/restore cycle from AmphibiousNodeEvaluator.
- Preserve the normal Mob pathfinding malus behavior for all non-amphibious evaluators.
- Keep the exp.6 nonblocking NearestBedSensor result handling and stale HOME revalidation.
- Retain Warext generator-scoped lock-free NodeEvaluator pools, bounded queues, adaptive CPU budgeting, tracker allocation reductions, and 4-vCPU burst protection.

## Upstream base
- Leaf 26.3 pinned at `0edc7f3b7d79b0e2e16a0ed1df8278c02c73537f`.
- Includes the October 5 async tracker fix and current 26.3 tracker/interpolation model.

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
