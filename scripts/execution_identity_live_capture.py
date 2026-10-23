#!/usr/bin/env python3
"""Sanitized Claude Code hook capture for real-host identity QA (HORO-1604).

Register as a hook command (per-invocation `--settings`, never the user's own
settings). Writes one JSON line per event to $QA_OUT: field *names* present,
8-hex hashes of identifier values, and the normalizer's scope. It never
writes prompts, tool input/output, transcript contents or paths. $QA_RUN
labels a run; $QA_KIND labels the hook type.
"""
import datetime, hashlib, json, os, re, sys
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
    _at = p.get("agent_type")
    rec["agent_type"] = _at if isinstance(_at, str) and re.fullmatch(r"[a-z0-9_-]{1,40}", _at) else ("other" if _at is not None else None)
    now = datetime.datetime.now(datetime.timezone.utc)
    idn = norm.normalize_claude_code_hook(p, host_id="qa-host", observed_at=now)
    scope = norm.narrowest_attributable_scope(idn)
    rec["normalized"] = {"scope": scope.value, "lineage": idn.lineage_status.value,
                         "has_agent": idn.agent_id is not None, "has_session": idn.provider_session_id is not None}
    try:
        rec["normalized"]["key_hash"] = h(idn.cache_key(scope))
    except Exception:
        rec["normalized"]["key_hash"] = None
out = os.environ.get("QA_OUT")
if not out:
    sys.exit("QA_OUT is required")
open(out, "a").write(json.dumps(rec, sort_keys=True) + "\n")
