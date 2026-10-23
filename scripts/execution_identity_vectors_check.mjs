// Second-language reference runner for the execution identity conformance vectors (HORO-1599).
//
// An independent re-implementation (no shared code with execution_identity.py) of envelope
// validation, cache keys, display ids, correlation and the payload normalizers. If this and the
// Python reference ever disagree on governance/product/execution-identity-conformance-vectors.json,
// the contract text is ambiguous, not either implementation wrong. Products in other languages
// (Rust: Fornax, Libra) mirror this file against the same vectors.
//
//   node scripts/execution_identity_vectors_check.mjs
//
// It reads only the committed vectors file next to this repo's governance docs and takes no arguments.
import crypto from "node:crypto";
import fs from "node:fs";

const VECTORS = new URL("../governance/product/execution-identity-conformance-vectors.json", import.meta.url);
const OPTIONAL_FIELDS = [
  "tool_instance_id", "provider_session_id", "agent_id", "turn_id", "parent_agent_id",
  "session_lineage_id", "event_id", "repo_id", "worktree_id",
];
const CORRELATION_FIELDS = [
  "host_id", "tool_provider", "tool_instance_id", "provider_session_id", "agent_id",
  "turn_id", "parent_agent_id", "session_lineage_id",
];
const PROVIDER_PATTERN = /^[a-z][a-z0-9_-]{0,31}$/;
const OBSERVED_AT_PATTERN = /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z$/;

const isString = (value) => typeof value === "string";
const compareStrings = (a, b) => Number(a > b) - Number(a < b);

function canonical(value) {
  if (Array.isArray(value)) {
    return value.map(canonical);
  }
  if (value !== null && typeof value === "object") {
    const entries = Object.keys(value).sort(compareStrings).map((key) => [key, canonical(value[key])]);
    return Object.fromEntries(entries);
  }
  return value;
}

const same = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));

function checkHeader(wire) {
  if (typeof wire.envelope_version !== "number" || wire.envelope_version !== 1) {
    throw new Error("envelope_version");
  }
  if (!OBSERVED_AT_PATTERN.test(wire.observed_at)) {
    throw new Error("observed_at");
  }
  if (!isString(wire.host_id) || wire.host_id === "") {
    throw new Error("host_id");
  }
  if (!isString(wire.tool_provider) || !PROVIDER_PATTERN.test(wire.tool_provider)) {
    throw new Error("tool_provider");
  }
}

function checkLineage(wire, lineage) {
  if (!["root", "child", "unknown"].includes(lineage)) {
    throw new Error("lineage_status");
  }
  if (lineage === "child" && !wire.parent_agent_id) {
    throw new Error("child without parent");
  }
  if (lineage !== "child" && wire.parent_agent_id != null) {
    throw new Error("parent without child");
  }
}

// Independent re-implementation of from_wire + the constructor invariants.
function parse(wire) {
  checkHeader(wire);
  const lineage = wire.lineage_status ?? "unknown";
  checkLineage(wire, lineage);
  const envelope = {
    envelope_version: 1,
    observed_at: wire.observed_at,
    host_id: wire.host_id,
    tool_provider: wire.tool_provider,
    lineage_status: lineage,
  };
  for (const field of OPTIONAL_FIELDS) {
    const value = wire[field];
    if (value === undefined || value === null) {
      continue;
    }
    if (!isString(value)) {
      throw new Error(field);
    }
    envelope[field] = value;
  }
  return envelope;
}

function projectKey(identity) {
  if (identity.worktree_id) {
    return [identity.worktree_id];
  }
  return identity.repo_id ? [identity.repo_id] : null;
}

function cacheKeys(identity) {
  const { host_id: host, tool_provider: provider, provider_session_id: session, agent_id: agent, turn_id: turn } = identity;
  const sessionKey = session ? [host, provider, session] : null;
  const agentKey = sessionKey && agent ? [...sessionKey, agent] : null;
  const turnKey = agentKey && turn ? [...agentKey, turn] : null;
  return { host: [host], session: sessionKey, agent: agentKey, turn_task: turnKey, project_worktree: projectKey(identity) };
}

function displayIds(identity) {
  const out = {};
  for (const dimension of ["provider_session_id", "agent_id", "turn_id", "host_id"]) {
    if (identity[dimension]) {
      const digest = crypto.createHash("sha256").update(`${dimension}:${identity[dimension]}`, "utf8").digest("hex");
      out[dimension] = `${dimension}:${digest.slice(0, 8)}`;
    }
  }
  return out;
}

function narrowestScope(identity) {
  const keys = cacheKeys(identity);
  for (const [name, scope] of [["turn_task", "turn_task"], ["agent", "agent"], ["session", "session"]]) {
    if (keys[name]) {
      return scope;
    }
  }
  return "unknown";
}

function correlates(left, right) {
  let concreteMatch = false;
  for (const field of CORRELATION_FIELDS) {
    if (left[field] == null || right[field] == null) {
      continue;
    }
    if (left[field] !== right[field]) {
      return false;
    }
    concreteMatch = true;
  }
  return concreteMatch;
}

function nativeValue(payload, key) {
  const isMapping = payload !== null && typeof payload === "object" && !Array.isArray(payload);
  const value = isMapping ? payload[key] : undefined;
  return isString(value) && value.trim() !== "" ? value : null;
}

function normalize(testCase) {
  const { provider, payload, host_id: hostId } = testCase;
  const result = {
    provider_session_id: nativeValue(payload, "session_id"),
    agent_id: nativeValue(payload, "agent_id"),
    turn_id: provider === "codex" ? nativeValue(payload, "turn_id") : null,
    lineage_status: "unknown",
  };
  const present = Object.entries(result).filter(([, value]) => value !== null && value !== "unknown");
  const identity = { host_id: hostId, tool_provider: provider, ...Object.fromEntries(present) };
  return { ...result, narrowest_attributable_scope: narrowestScope(identity) };
}

function refused(wire) {
  try {
    parse(wire);
  } catch {
    return true;
  }
  return false;
}

function run(document) {
  const failures = [];
  const identities = {};
  for (const item of document.valid) {
    const identity = parse(item.wire);
    identities[item.name] = identity;
    const { expect } = item;
    const checks = [
      ["round_trip", identity, expect.round_trip],
      ["cache_keys", cacheKeys(identity), expect.cache_keys],
      ["display_ids", displayIds(identity), expect.display_ids],
      ["narrowest_attributable_scope", narrowestScope(identity), expect.narrowest_attributable_scope],
    ];
    for (const [label, got, want] of checks) {
      if (!same(got, want)) {
        failures.push(`${item.name} ${label}`);
      }
    }
  }
  for (const item of document.invalid) {
    if (!refused(item.wire)) {
      failures.push(`${item.name} should be refused`);
    }
  }
  for (const item of document.correlations) {
    if (correlates(identities[item.left], identities[item.right]) !== item.expect.correlates) {
      failures.push(item.name);
    }
  }
  for (const item of document.normalizers) {
    if (!same(normalize(item), item.expect)) {
      failures.push(`${item.name} normalizer`);
    }
  }
  const total = document.valid.length + document.invalid.length + document.correlations.length + document.normalizers.length;
  return { total, failures };
}

const { total, failures } = run(JSON.parse(fs.readFileSync(VECTORS, "utf8")));
for (const failure of failures) {
  console.log(`MISMATCH ${failure}`);
}
console.log(`checked ${total} cases, mismatches=${failures.length}`);
process.exit(failures.length === 0 ? 0 : 1);
