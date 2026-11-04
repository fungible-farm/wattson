# PowerCoder consolidation staging — not a runtime migration pass

The owner of model training, inference/evaluation, its tests and the PowerCoder
capabilities is **fungible-farm/wattson**. The lab repository must eventually consume
Wattson through an explicit versioned interface rather than own another implementation.

This branch preserves the missing/differing inputs for that cutover. It does not
activate a second `powercoder` package or overwrite Wattson's already newer training
implementation. `source/` is an exact source snapshot for review, excluded from the
Wattson wheel and normal pytest discovery. Its old paths/imports are intentionally
unchanged so the SHA-256 record identifies what was preserved; these files are
**not** claimed to run from this directory.
Original whitespace is preserved too: the captured PowerCoder README has an
existing extra blank line at EOF. New migration documentation and verifier code
pass the whitespace check separately.

`manifest.json` maps legacy files to proposed canonical locations and records both
identities where a canonical file exists. The source scan includes the local
uncommitted `training/agent_sft.py`, all local training YAMLs and the PowerCoder
README. Wattson already includes the supervised-tail loss, served-history reasoning
fix, and most newer YAMLs, with data/output paths adapted to its own tree. Preserve
those canonical changes when resolving overlaps.

```sh
# Does not need the old repository, network, a model, or GPU.
uv run python scripts/check_powercoder_snapshot.py
```

This is a preservation gate only. Still required:

1. Resolve each missing/different module into `src/wattson`, each test into `tests`,
   configuration into `configs`, container into `containers`, and fixtures into
   their real packaged/test resource locations. The proposed mapping needs review;
   it is not approval to copy every file verbatim.
2. Replace old package imports and repo-root calculations; remove runtime access
   to the lab's `labs/`, schemas, Rust IR, data paths and scripts. Identify lab-only
   adapters explicitly rather than pull the entire lab into Wattson.
3. Audit the reference inventory for launchers, Justfile recipes, Rust/schema and
   lab/test integration outside the two main source directories. Copying those two
   directories alone would leave the repositories coupled.
4. Run source/import checks, the canonical CPU suite, migrated test groups, then
   container and training preflights. GPU training and physics fixtures need the
   declared interpreter/data/model versions; no training run is claimed here.
5. Validate a standalone Wattson checkout with the lab directory unavailable.
   Then remove superseded implementation from a separate lab cleanup branch and
   update that repository's commands/dependencies to use the versioned interface.

No old source was deleted during the shift correction. This prevents an unvalidated
cutover from discarding working behavior. Checkpoints, weights, caches, downloaded
case files and transient results are not Git source inputs; keep their artifact
identities/locations and move them through the model OCI/data packaging workflow.

The preceding DevEx branch is `staged/wattson-airgap-devex`. Its four branches were
accidentally pushed to nem-poweragent-lab first, then transplanted onto Wattson's
own main history. The accidental remote branches were deleted with leases checking
their expected commit IDs. Neither default branch was changed.
