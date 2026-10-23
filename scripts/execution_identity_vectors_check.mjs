// Second-language reference runner for the execution identity conformance vectors (HORO-1599).
//
// An independent re-implementation (no shared code with execution_identity.py) of envelope
// validation, cache keys, display ids, correlation and the payload normalizers. If this and the
// Python reference ever disagree on governance/product/execution-identity-conformance-vectors.json,
// the contract text is ambiguous, not either implementation wrong. Products in other languages
// (Rust: Fornax, Libra) mirror this file against the same vectors.
//
//   node scripts/execution_identity_vectors_check.mjs governance/product/execution-identity-conformance-vectors.json
import fs from "node:fs"; import crypto from "node:crypto";
const doc = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const OPT = ["tool_instance_id","provider_session_id","agent_id","turn_id","parent_agent_id","session_lineage_id","event_id","repo_id","worktree_id"];
const isStr = v => typeof v === "string";
function parse(w) {                       // independent re-implementation of from_wire + __post_init__
  if (!(Number.isInteger(w.envelope_version) && typeof w.envelope_version === "number" && w.envelope_version === 1)) throw new Error("version");
  const lin = w.lineage_status ?? "unknown"; if (!["root","child","unknown"].includes(lin)) throw new Error("lineage");
  if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+Z$/.test(w.observed_at)) throw new Error("observed_at");
  if (!isStr(w.host_id) || !w.host_id) throw new Error("host");
  if (!isStr(w.tool_provider) || !/^[a-z][a-z0-9_-]{0,31}$/.test(w.tool_provider)) throw new Error("provider");
  for (const k of OPT) if (w[k] !== undefined && w[k] !== null && !isStr(w[k])) throw new Error(k);
  if (lin === "child" && !w.parent_agent_id) throw new Error("child");
  if (lin !== "child" && w.parent_agent_id != null) throw new Error("parent");
  const o = { envelope_version: 1, observed_at: w.observed_at, host_id: w.host_id, tool_provider: w.tool_provider, lineage_status: lin };
  for (const k of OPT) if (w[k] != null) o[k] = w[k];
  return o;
}
const keys = i => ({
  host: [i.host_id],
  session: i.provider_session_id ? [i.host_id, i.tool_provider, i.provider_session_id] : null,
  agent: i.provider_session_id && i.agent_id ? [i.host_id, i.tool_provider, i.provider_session_id, i.agent_id] : null,
  turn_task: i.provider_session_id && i.agent_id && i.turn_id ? [i.host_id, i.tool_provider, i.provider_session_id, i.agent_id, i.turn_id] : null,
  project_worktree: i.worktree_id ? [i.worktree_id] : i.repo_id ? [i.repo_id] : null,
});
const disp = i => { const o = {}; for (const d of ["provider_session_id","agent_id","turn_id","host_id"]) if (i[d]) o[d] = d + ":" + crypto.createHash("sha256").update(d + ":" + i[d], "utf8").digest("hex").slice(0, 8); return o; };
const narrowest = i => { const k = keys(i); return k.turn_task ? "turn_task" : k.agent ? "agent" : k.session ? "session" : "unknown"; };
const corr = (a, b) => { let m = false; for (const f of ["host_id","tool_provider","tool_instance_id","provider_session_id","agent_id","turn_id","parent_agent_id","session_lineage_id"]) { if (a[f] == null || b[f] == null) continue; if (a[f] !== b[f]) return false; m = true; } return m; };
const nat = (p, k) => (p && typeof p === "object" && !Array.isArray(p) && isStr(p[k]) && p[k].trim() ? p[k] : null);
const canon = v => Array.isArray(v) ? v.map(canon) : v && typeof v === "object" ? Object.fromEntries(Object.keys(v).sort().map(k => [k, canon(v[k])])) : v;
const eq = (a, b) => JSON.stringify(canon(a)) === JSON.stringify(canon(b));
let bad = 0, n = 0; const fail = (m) => { bad++; console.log("MISMATCH", m); };
const by = {};
for (const c of doc.valid) { n++; const i = parse(c.wire); by[c.name] = i;
  if (!eq(i, c.expect.round_trip)) fail(c.name + " round_trip");
  if (!eq(keys(i), c.expect.cache_keys)) fail(c.name + " keys");
  if (!eq(disp(i), c.expect.display_ids)) fail(c.name + " display");
  if (narrowest(i) !== c.expect.narrowest_attributable_scope) fail(c.name + " narrowest"); }
for (const c of doc.invalid) { n++; let ok = false; try { parse(c.wire); } catch { ok = true; } if (!ok) fail(c.name + " should be refused"); }
for (const c of doc.correlations) { n++; if (corr(by[c.left], by[c.right]) !== c.expect.correlates) fail(c.name); }
for (const c of doc.normalizers) { n++;
  const r = { provider_session_id: nat(c.payload, "session_id"), agent_id: nat(c.payload, "agent_id"), turn_id: c.provider === "codex" ? nat(c.payload, "turn_id") : null, lineage_status: "unknown" };
  const i = { host_id: c.host_id, tool_provider: c.provider, ...Object.fromEntries(Object.entries(r).filter(([, v]) => v != null)) };
  r.narrowest_attributable_scope = narrowest(i);
  if (!eq(r, c.expect)) fail(c.name + " normalizer " + JSON.stringify(r)); }
console.log(`checked ${n} cases, mismatches=${bad}`); process.exit(bad ? 1 : 0);
