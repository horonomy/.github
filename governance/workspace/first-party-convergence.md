# First-party product convergence

This is the authority boundary for deliberate synchronization of Horonom-owned
product source, installed artifacts, and running processes. It is not a package
manager and it is not an inventory of everything installed on a workstation.

## Three registries, three questions

- `metadata/company.yaml` answers what Horonom publicly presents as a product.
- `governance/workspace/manifest.yaml` answers where known Horonom repositories
  belong in an optional local workspace.
- `governance/workspace/first-party-products.yaml` is the positive allowlist of
  product runtimes this convergence tool may inspect or operate.

Presence in a checkout, configuration repository, PATH, plugin cache, skill
folder, MCP configuration, or dependency graph grants no authority. Anything
absent from the first-party inventory is `UNKNOWN` and is left untouched.

Archify (`tt-a1i/archify`) and Visual Explainer
(`nicobailon/visual-explainer`) are explicitly third-party. They appear only in
metadata-only exclusion rows so reports show the boundary. Those rows contain no
commands and can never produce fetch, validation, installation, or process
actions. Their existing third-party release, plugin, and configuration policies
continue unchanged.

The following are also outside this mechanism: Claude/Codex skills and plugins,
MCP servers, language runtimes, development tools, package dependencies, and
arbitrary repositories. `coding-agent-environment`, `ca-claude`, directory
profiles, provider-agnostic model routing, and corporate/personal isolation keep
their current owners and behavior.

## Desired revision

For every managed first-party product:

```text
desired_revision = latest validated configured remote base-branch HEAD
```

The canonical remote is selected by exact `github.com/horonomy/<repo>` identity,
not by the name `origin`. The base branch is resolved with `git ls-remote
--symref <remote> HEAD`. A recorded commit SHA proves what was deployed; it never
pins the product after the configured base branch advances.

A feature, ticket, worktree, detached, or locally repaired commit is never a
deployment target, even when its tests pass. If the remote base HEAD fails a
required validation gate, its state is `BASE_HEAD_NOT_DEPLOYABLE`; installation
and daemon reconciliation stop until a fix is merged normally and the new base
HEAD validates.

## Statuses

- `CURRENT`: remote base HEAD, local base checkout, independently measured
  installed artifact, and running process all identify the exact same SHA, and
  required validation passed at that SHA.
- `DRIFTED`: one or more positively measured revision differs. A binary,
  receipt, hooks, or healthy daemon existing does not make it current.
- `UNVERIFIABLE`: required identity evidence is absent or ambiguous. This is
  never treated as current and never guessed from version strings or process
  existence.
- `BASE_HEAD_NOT_DEPLOYABLE`: the exact remote base HEAD failed a declared
  required validation command. No installation or restart may follow.

Products may be positively first-party while remaining `unverifiable` or
`not_present` in the inventory. That records ownership without inventing an
installed/running revision contract.

## Command lifecycle

```text
python3 scripts/first_party_converge.py check --root <workspace> [--validate]
python3 scripts/first_party_converge.py plan  --root <workspace>
python3 scripts/first_party_converge.py apply --root <workspace>
```

`check` and `plan` are read-only. `apply` re-resolves the canonical remote,
default branch, and SHA; rejects dirty tracked checkouts, linked worktrees,
divergence, symlink ambiguity, stale inventory, and failed validation; and
fast-forwards only. It invokes only the installer declared by the product and
measures the result rather than trusting an installer success message.

A running process is restarted only after successful installation, only when
its prior revision is positively known to differ, and only through the
product-declared lifecycle. A matching healthy process is reused. An
unverifiable process is not broad-killed or restarted on inference.

Local convergence receipts are owner-only runtime evidence under
`~/.local/state/horonom/convergence/` (or
`$HORONOM_CONVERGENCE_STATE_DIR`). They remain outside Git and contain only the
product ID, deployed source SHA, installed artifact hash, schema version, and
installation time. Workstation paths, logs, prompts, credentials, private
configuration, and runtime evidence must never be committed or exported.

## Reports

Every report has two separate sections:

1. **FIRST-PARTY PRODUCTS** — the allowlisted products and their
   remote/local/installed/running revision matrix.
2. **THIRD-PARTY TOOLS** — metadata-only exclusions, explicitly marked
   `EXCLUDED; NO ACTIONS`.

The second section is proof of non-authority, not another update queue.
