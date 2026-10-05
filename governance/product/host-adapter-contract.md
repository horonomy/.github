# Host adapter contract v1

This document defines host facts and adapter boundaries shared by Fornax,
Circinus, and Libra. It does not define product evidence verdicts, authorization
policy, budget interpretation, or billing settlement. Native observation is
normalized into factual events; each product binds those facts to its existing
truth and decision systems.

The machine-readable schemas and authored vectors are under
[`host-adapter/v1`](./host-adapter/v1/). The checked-in vectors are synthetic
contract-shape checks, not proof of real host behavior. A separate isolated
native Codex audit/probe observed allow, deny, rewrite, malformed/timeout
fail-open behavior, and an untrusted hook being skipped; fixture provenance
and limits of that evidence are recorded in the fixture README.

## Existing contracts remain authoritative

- [`execution-identity-contract.md`](./execution-identity-contract.md) v1
  owns execution identity, unknown attribution, and field meanings. Adapters
  map into that envelope; they must not fork or infer its lineage rules.
- [`host-config-ownership-test-contract.md`](./host-config-ownership-test-contract.md)
  and [`host-config-safety-ux-disclosure.md`](./host-config-safety-ux-disclosure.md)
  own preservation and user-facing safety requirements. Unknown or unowned
  config remains preserved; uninstall removes only verified owned deltas.
- [`statusline-provider-contract.md`](./statusline-provider-contract.md)
  owns status-provider scope and rendering semantics.
- [Fornax ADR-0023](https://github.com/horonomy/fornax-core/blob/main/docs/adr/0023-external-adapter-registry.md) and its execution boundary own
  registration and executable dispatch. Executable adapters run only through
  the separately invoked `fornax-host-adapter-exec`; Fornax core stays passive
  and does not spawn adapters. Trusted subprocess code has ambient OS
  authority; a digest is an identity/drift check, not a sandbox.
- [Internal-docs ADR-0014](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0014-cross-product-host-wide-one-writer-lock.md)
  owns common lock ordering and write/readback behavior. Apply acquires that
  lock before authoritative read and plan, retains it through verification
  and receipt cleanup, and never applies a stale preview or proceeds unlocked.
  Exact lock identity and backoff remain defined there.
- [Internal-docs ADR-0013](https://github.com/horonomy/internal-docs/blob/main/docs/engineering/adr-0013-cli-experience-v1-contract.md)
  owns CLI help purity, package-derived version, and non-TTY error
  conventions. Help must not read state. Ordinary list/inspect may passively
  read registry and receipts but must not execute adapter code.

## Optional roles and host facts

Adapters may implement any subset of these roles: `LifecycleSource`,
`ToolBoundarySource`, `PolicyControlSink`, `UsageEvidenceSource`,
`IdentitySource`, `OperatorSurface`, and `ConfigDriver`. The role names describe
the host boundary only. They do not imply that a product consumes that role.
An adapter advertises narrow capability keys, such as `tool.pre.observe`,
`tool.pre.deny`, `permission.request.approve`, `usage.tokens`, or
`identity.agent`.

`HostCapabilitySnapshot` v1 is a contextual observation containing adapter and
host identity, context, lifecycle facts, and capability records. Its six
states are exactly `supported`, `limited`, `trust-required`, `admin-disabled`,
`unavailable`, and `unknown`. Registration, installation, desired enablement,
adapter-code trust, host trust, and observed execution are separate facts.
Declaration alone never establishes `supported`; positive support needs fresh,
context-matching evidence. Absence and untested behavior remain explicit.
`enabled:false` or a feature-off state is `unavailable` with a distinct reason,
not an administrator prohibition. Reasons are retained. If several causes
apply, primary state follows this order: verified administrator prohibition;
required but ungranted trust; verified unavailability or disablement;
unresolved prerequisites; verified limited support; verified full support.
All applicable reasons remain present. Declarations, shape-only, stale, or
conflicting evidence cannot produce `supported` or `limited`. Positive evidence
expires or is invalidated by scope/profile, trust, configuration,
executable/entrypoint/manifest, adapter-version, or host-version changes. A
capability's `limits` has defined optional keys `event_classes`,
`tool_classes`, `execution_mode` (`synchronous`, `asynchronous`, `mixed`,
`unknown`), and `failure_behavior` (`fail_open`, `fail_closed`, `skip`,
`unknown`). Other provider-specific limit keys are extensions without shared
normative meaning. Freshness,
context, and trust remain runtime checks, not schema facts.

Evidence records identify a bounded source and result/digest. They must not
contain secrets, whole config files, raw host payloads, commands, prompts,
environment values, or unbounded paths. A successful callback demonstrates
only the event/control class and context exercised. Trust/config/profile/
executable/version changes invalidate affected positive observations.

`CanonicalHostEvent` v1 has a closed `kind` set: `lifecycle`, `tool_before`,
`tool_after`, `tool_failure`, or `usage`. Each kind has typed factual fields.
Unknown native shapes are rejected or quarantined; they are not converted to
empty valid events. `quality` records `literal`, `reconstructed`, or
`heuristic` provenance, and field mappings are named in `field_provenance`.
Raw native input is transient. No event asserts a product verdict or policy
decision.

Identity uses execution-identity v1. Missing provider-native IDs remain absent;
unknown lineage remains unknown. In particular, cwd, PID, timing, or nearby
processes never establish parent/child identity. Event IDs identify an
observation. A replay key is allowed only when backed by a native event ID or
durable source position; equal content, command, cwd, or time is insufficient.
Products own transactional deduplication of irreversible evidence or ledger
effects. Usage facts distinguish `delta` from `cumulative`, carry units and
observation scope, and are not billing settlement.

Tool boundary facts require a nonempty native `tool_name` and preserve it verbatim as provenance; it is
not a normalized action (for example, a native shell alias is not itself a
complete command). Optional normalized action kinds are `shell_command`,
`file_mutation`, and `unknown`, with optional `operand_completeness` of
`complete`, `partial`, or `unknown`. If action facts are absent, the observation
is record-only and product bindings report the relevant coverage unavailable.
Complete operands require a known normalized action, actual typed operands,
and `operands_availability:"observed"`; availability is one of `observed`,
`unavailable`, `redacted`, or `not_provided`. Missing action, completeness, or
availability remains record-only and never dispatches by native tool name or
establishes coverage. Shell commands carry a nonempty complete command string;
file mutations carry a nonempty list of exact operation/path records, and a
move requires both endpoints. A destination is valid only for a move. Native patch-based
adapters must parse the complete patch grammar through EOF and account for all
affected paths before claiming complete operands. A payload that cannot
provide complete applicable operands remains partial/unknown and cannot be
treated as a fully understood action. Product bindings perform product-local
classification; the host adapter does not implement product policy.

## Executable manifest and protocol

`host-adapter-manifest` v1 is distinct from the legacy data-only declarative
manifest. Its schema is closed at every object boundary: unknown fields,
including execution-bearing additions, are rejected. The descriptor binds
stable adapter ID, adapter version, protocol versions, host SPI contract
range, roles/capabilities, host constraints, inline driver configuration
schema, absolute executable, literal argv, declared runtime file digests,
bounded input size, and declared environment/path needs. No shell,
interpolation, environment expansion, PATH discovery, or downloaded launcher
is part of this contract. Runtime dependencies not included in the declared
digest set prevent a claim of complete implementation identity.

Registration validates and copies manifest bytes without execution. Explicit
trust binds the manifest, normalized invocation, resolved executable and
declared executable entrypoint/dependency bytes. Any relevant drift invalidates
trust before another spawn. This does not grant native host-hook trust and does
not restrict trusted code's ambient OS authority. Duplicate adapter IDs and
builtin-shadowing IDs are rejected before any registry mutation. The
interpreter entrypoint digest is required in addition to the interpreter hash.
The bounded child process is cancelled and reaped on timeout or cancellation;
the negotiated handshake is cached only against manifest, invocation,
implementation identity, and both selected contract versions.

Manifest version, transport protocol version, and host SPI/data contract
version are separate. `host_version_constraints` has at least one closed
`{provider,minimum,maximum}` entry. Provider identifiers remain open strings;
each unique entry constrains one family and multiple families are alternatives.
Bounds are nullable and inclusive. Non-null bounds use strict SemVer 2.0.0
precedence and minimum cannot exceed maximum; build metadata does not affect
precedence. An absent or unparseable version under a constraint is unknown.
Opaque host-version conventions use `null` bounds and report contextual tested
limits. An unknown provider is valid metadata, not evidence that a host exists.

`contract_version_range` is a required closed inclusive range of host SPI/data
contract versions, each an integer from 1 through 2147483647, with minimum no
greater than maximum. `protocol_versions` has 1..32 unique positive integers
in that range. A valid future-only declaration may be registered and
inspected as incompatible, but not executed, activated as usable, or treated
as effective support. The runner offers only the intersection of declared and
supported transport versions, selects its highest version, and independently
selects the highest supported host contract version in range. Incompatibility
is refused before spawn as `unsupported_version` with reason
`protocol_version_incompatible` or `host_contract_version_incompatible`.

`configuration_schema` is inline adapter settings data, separate from
`plan_config.validator_ref`, the installed allowlisted product request/plan
validator. It follows the bounded JSON Schema Draft 2020-12 profile: every
node is an object with exactly one type from `object`, `array`, `string`,
`integer`, `number`, `boolean`, or `null`; the root is object. Allowed node
keywords are `type`, `properties`, `required`, `additionalProperties`,
`items`, `enum`, `const`, `minimum`, `maximum`, `exclusiveMinimum`,
`exclusiveMaximum`, `minLength`, `maxLength`, `minItems`, `maxItems`,
`minProperties`, `maxProperties`, `title`, and `description`, plus root-only
`$schema` with the exact Draft 2020-12 URI. Object nodes require
`additionalProperties`; it is boolean or one schema node. `items` is one
schema node, `required` is unique strings, enum contains 1..64 unique scalar
values, const is scalar, and annotations are bounded to 1024 characters.
Numeric bounds apply only to integer/number nodes; cardinality bounds apply to
their matching string, array, or object node. Inclusive and exclusive numeric
endpoints are considered together, and contradictory bounds that admit no
value are rejected.
The bundled validator implements the schema after profile and size checks.
Unknown keywords, references (`$ref`, `$dynamicRef`, `$id`, `$defs`, anchors),
patterns/regex, formats, combinators/conditionals, defaults, content decoding,
custom vocabularies, and code-bearing keywords are rejected. A literal
configuration key named `$ref` under `properties` remains ordinary data. The
dialect URI is never fetched. Registration checks schema without execution or
network access; rejection has zero registry mutation. No-config adapters use
an object schema with empty properties and `additionalProperties:false`.

Both the schema and effective settings are limited to 64 KiB UTF-8 JSON, depth
16, and 4096 JSON nodes. Runtime input byte limits are checked before UTF-8
decoding. Duplicate keys, non-finite numbers, malformed JSON, and overflow are
rejected. No secret or secret default appears in manifest or schema
annotations. Operation requests may include `configuration`; omission means
`{}`. The runner validates before spawn and passes it verbatim without
default insertion or shell/environment expansion. Invalid values or missing
required settings return `invalid_input` with bounded key/reason diagnostics,
never echoed values. Settings cannot override launch, runtime files,
environment permissions, trust, or selected versions. Setting changes
invalidate affected capability evidence; the manifest digest covers schema
changes. Products use their existing settings lifecycle boundary.

Each request is one bounded process invocation, UTF-8 JSON on stdin and one
JSON response on stdout; diagnostics use bounded stderr. Defaults are 1 MiB
input/output, 16 KiB diagnostic capture, and 2 seconds for explicit handshake
or probe. Oversize, timeout, malformed/multiple stdout documents, wrong request
ID/version, nonzero exit, unsupported operation, or identity drift are typed
failures. No automatic mutation retry is allowed. Read-only retries, if any,
are explicit and bounded.

The handshake request is `{protocol:"horonom.host-adapter",
offered_versions:[1],request_id,operation:"handshake"}`. A successful response
echoes protocol/request ID and includes `selected_version:1`; no common version
returns `error.code:"unsupported_version"`. Subsequent requests carry
`protocol_version:1`, `host_contract_version:1`, request ID, one operation
(`probe`, `normalize`, `encode_control`, or `plan_config`), typed input, and
optional validated configuration. Responses echo request ID and both versions
and carry exactly one of `result` or `error`. Error codes
are closed; unknown response extension fields are ignored, but unknown required
versions/enums, control actions, or result types are refused. Transport version
and native host payload schema version are distinct.

The root protocol schema is a request/response union. Reserved fields make
handshake success/error and operation success/error mutually exclusive;
unknown nonreserved response extensions remain forward-compatible. Runtime
validation checks responses against both pending request and result type,
binds ID and selected versions, and enforces limits, deadline, exit status,
and identity drift. JSON Schema cannot establish freshness, authorization, or
runtime evidence.

The operation types are normative. `probe` takes `{context:SnapshotContext}`
and returns `{snapshot:HostCapabilitySnapshot}`; runner code verifies adapter
identity, context, lifecycle, and authoritative trust, and a driver cannot
grant its own trust. `normalize` takes `{native_payload:object,
source:CanonicalEventSource,host_id,observed_at,
capability_snapshot:HostCapabilitySnapshot}` and returns a nonempty
`{events:[CanonicalHostEvent,...]}`. Events match the supplied observation
context, snapshot, host, and registered adapter; IDs distinguish observations,
not side effects. Invalid or unsupported inputs never become empty success.

`encode_control` takes `{intent:ControlIntent,event:CanonicalHostEvent,
capability_snapshot:HostCapabilitySnapshot}` and returns a closed
`ControlEncodingResult`: encoded `{schema_version:1,status:"encoded",event_id,
action,native_response:object,delivery:"encoded"}` or unsupported
`{schema_version:1,status:"unsupported",event_id,action,reason_code,
delivery:"not_attempted"}`. `ControlIntent` is closed and carries schema
version, protocol request ID, original event ID, stable product ID,
product-owned decision reference, action, nonempty unique required capability
keys, product-qualified failure class, and optional rewrite input. Rewrite
input is required only for rewrite and contains the complete proposed native
input, validated against the applicable native schema and evidenced rewrite
capability. Unknown schemas and unrecognized failure classes are unsupported,
never weakened to a default. Products publish the failure class they use
before enabling the operation.

`request_approval` means an evidenced host approval boundary. It is neither a
product Review verdict nor Codex PreToolUse `ask`. Products choose fallback;
the encoder cannot create pending approval, change mode, invent authority, or
translate Review into allow. Codex PreToolUse currently exposes evidenced
allow/deny/rewrite only, so approval is unavailable there. Encoder results
cannot claim host acknowledgment or prevented effects; delivery and effect
observations belong to the caller and need separate evidence.

`plan_config` takes `{product_id,validator_ref,context,request}` and returns
`{product_id,validator_ref,plan}`. `ValidatorRef` is a closed `{id,version,
digest}` for an installed allowlisted product validator, never a URL to fetch.
Missing or mismatched validators are unsupported with zero application. The
product validator checks request and plan shapes; the existing writer checks
target and ownership. No generic config-patch language is defined.

## Control and config boundary

`PolicyControlSink` only encodes a product-provided intent against an original
event and evidenced capability. It does not decide policy. Unsupported
allow/deny/rewrite/approval actions return typed unsupported/error; they never
become silent allow or an invented approval. Delivery is separately reported
as `not_attempted`, `encoded`, `host_acknowledged` only when actually
acknowledged, or `unknown` as caller-side observations. Encoder responses only
report `encoded` or `not_attempted`. An encoded deny is not proof of prevention.

`ConfigDriver` plans an exact ownership delta. The product-owned format-aware
writer validates/applies it, preserves every unowned/unknown field, re-reads
and verifies the target, and reports actual partial outcomes. Malformed or
unsupported formats are zero mutation. Do not restore a stale whole-file
snapshot. Apply acquires the common ADR-0014 lock before authoritative read and
planning and retains it through verification and receipt cleanup. It never
applies a stale preview or proceeds unlocked; exact lock identity and backoff
are defined by ADR-0014.

## CLI envelope and lifecycle

The CLI JSON envelope carries schema version, operation, adapter ID, optional
scope/profile, outcome, reason codes, optional plan/result, and verification
state. Existing product commands and aliases remain compatibility entry
points. Help does not read state. Ordinary list/inspect may passively read
registry, receipts, and cached capability data. These operations do not execute
adapter code, install software, prompt for trust, start a daemon, or make cloud
calls. Explicit probe/doctor may execute only already-trusted code from its
designated boundary. In Fornax that is the separate exec binary; core CLI reads
receipts/descriptors and remains passive.

Canonical lifecycle verbs are `list`, `inspect`, `capabilities`, `register`,
`unregister`, `install`, `uninstall`, `enable`, `disable`, `status`, `doctor`,
and `explain`; `plan` remains available where supported. New registration is
distinct from install and trust. New install starts disabled; enabling does not
grant trust. Disable retains registration/install state; uninstall removes
only owned installed state; unregister refuses while enabled or installed.
Legacy command behavior is preserved and reports these dimensions separately.

Mutators support dry-run and validated scope/profile where applicable.
Unsupported scope/profile is an error, never fallback. Digest-bound explicit
trust is not bypassed by a generic yes flag. Plans redact secret values while
retaining ownership/diff meaning. Adapter execution and host configuration
remain explicit operator actions.

## Compatibility and evidence limits

Schema version and required enum changes that affect safety require an explicit
versioned contract update. Additive optional fields on identity and protocol
responses remain forward-compatible according to their owning contracts.
Unknown execution-bearing manifest additions are incompatible and rejected.
An implementation must test old-reader/additive-field behavior and negative
version/control cases. Fixtures are authored synthetic examples, not empirical
success claims. The isolated source audit separately observed Codex allow,
deny and rewrite vectors, malformed/timeout fail-open behavior, and an
untrusted hook being skipped. It does not establish real PermissionRequest,
subagent, asynchronous, administrator-disabled-hook, or sandbox behavior, nor
universal enforcement; these remain unverified pending isolated capture.
