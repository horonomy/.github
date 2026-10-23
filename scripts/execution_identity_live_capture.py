#!/usr/bin/env python3
"""Sanitized Claude Code hook capture for real-host identity QA (HORO-1604).

Register as a hook command (per-invocation `--settings`, never the user's own
settings). Writes one JSON line per event to $QA_OUT: field *names* present,
8-hex hashes of identifier values, and the normalizer's scope. It never
writes prompts, tool input/output, transcript contents or paths. $QA_RUN
labels a run; $QA_KIND labels the hook type.
"""
import datetime, hashlib, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import execution_identity_normalize as norm

def h(v):
    return hashlib.sha256(str(v).encode()).hexdigest()[:8]

raw = sys.stdin.read()
rec = {"run": os.environ.get("QA_RUN", "?"), "kind": os.environ.get("QA_KIND", "hook")}
try:
    p = json.loads(raw)
except Exception:
    p = None
rec["parsed"] = isinstance(p, dict)
if isinstance(p, dict):
    rec["event"] = p.get("hook_event_name")
    rec["fields"] = sorted(p.keys())
    rec["ids"] = {k: h(p[k]) for k in ("session_id", "agent_id", "turn_id", "prompt_id", "tool_use_id", "parent_agent_id", "parent_session_id") if isinstance(p.get(k), str)}
    rec["agent_type"] = p.get("agent_type") if isinstance(p.get("agent_type"), str) else None
    now = datetime.datetime.now(datetime.timezone.utc)
    idn = norm.normalize_claude_code_hook(p, host_id="qa-host", observed_at=now)
    scope = norm.narrowest_attributable_scope(idn)
    rec["normalized"] = {"scope": scope.value, "lineage": idn.lineage_status.value,
                         "has_agent": idn.agent_id is not None, "has_session": idn.provider_session_id is not None}
    try:
        rec["normalized"]["key_hash"] = h(idn.cache_key(scope))
    except Exception:
        rec["normalized"]["key_hash"] = None
open(os.environ["QA_OUT"], "a").write(json.dumps(rec, sort_keys=True) + "\n")
