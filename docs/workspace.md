# Captured project workspace

The consumer project is the workspace. A contract does not declare a workspace
directory or enumerate its source files. `needs` names required inputs and
validated upstream handoffs; `produces` names delivery artifacts. Selecting a
package never makes that package's checkout or cache the consumer project.

## Selection profile

The versioned profile `apmx-project-selection/1` captures tracked working-tree
files, including local modifications, and eligible untracked files. Deleted
tracked files stay deleted. Non-Git projects use the same discovery behavior.
New ordinary source files therefore need no change to a contract's `needs`.

Git's own ignore parser evaluates `.gitignore` files inside the consumer root,
including nested rules and negation. Tracked files remain selected even when an
ignore rule matches them. Selection does not use global ignore files,
`.git/info/exclude`, or ignore rules above the consumer root. A temporary empty
Git index supplies these semantics without initializing or changing the caller
repository. Git remains an execution prerequisite.

The following directory names are excluded at every depth, case-insensitively:
`.git`, `.apm`, `apm_modules`, `.venv`, `venv`, `node_modules`, `__pycache__`,
`.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.tox`, `.nox`, `_apmx_source`,
and `_apmx_context`. Consumer native-skill trees (`.agents/skills`,
`.github/skills`, `.claude/skills`), local dependency roots identified by the
effective lock, and the selected package's original source root also stay out.
Skills are supplied only through selected imports.

Contracts, `checks/`, manifests, locks and selected imported capabilities remain
separately admitted resources, not implicit editable application files. Their
existing collision and integrity checks still apply. An ignored or excluded
required application input is an error before inference, not an omitted input
or permission to bypass an ignore rule.

All graph outputs, including unexecuted sibling outputs, are excluded from the
initial project. Downstream inputs come from captured, validated predecessor
artifacts, never stale copies in the caller. A focused leaf can instead receive
an upstream document as an explicit caller input.

Selection never expands into parent or sibling projects. Selected symlinks,
case-colliding paths, nested repositories, unsupported submodules, unsafe paths
and file/count/byte-limit violations refuse capture. The default project limits
are 10,000 files, 128 MiB total and 8 MiB per file. These are separate from the
16 explicit-input limit and the bounded checker-resource limits. No list is
silently truncated.

**Ignore rules are not a secret scanner.** An ordinary unignored file, including
an unignored secret, can be captured and made available to the producer. Inspect
the consumer and its ignore rules before running trusted work. Capturing files
does not place the native harness or checks in a sandbox.

## Frozen bytes and records

A factory captures its original application once, before the first producer,
under `.apm/chains/<id>/project/`. Every stage receives a fresh private copy of
those bytes plus only its selected resources and authorized file handoffs.
Changing the caller application during a factory run cannot change a later
stage's original baseline. Changed contracts, checker resources, policies,
selected imports or retained project bytes still halt execution.

The chain's `project_capture` records the selection profile, original Git HEAD
when present, sorted paths, SHA-256 hashes, sizes, modes and inventory digest.
Each leaf baseline records `selection_schema` and `project_digest` in addition
to its complete baseline and checker inventories. The project digest excludes
stage handoffs and separately admitted resources. Its encoding is SHA-256 over
compact UTF-8 JSON of sorted `[path, sha256, size, mode]` rows, using JSON's
ASCII escaping. No timestamp or temporary path enters that digest.

Successful aggregate artifact views retain the original captured application,
required resources and admitted outputs, so independent checks can reconstruct
the delivered candidate without the live caller. The original caller is never
patched automatically. These are additive recorded observations; historical
records without the selection fields do not acquire implicit-project semantics
and are not rewritten.

The capture can be reused by a future repair controller, but this change does
not itself add retry budgets, OpenCode execution, packaged-factory archive
support or standards Evidence Package exports.
