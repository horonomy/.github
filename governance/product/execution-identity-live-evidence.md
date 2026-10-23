# Execution identity: real-host evidence log (HORO-1604)

Sanitized text evidence only: field **names**, 8-hex hashes of identifier values and the reference
normalizer's result. No prompts, tool input/output, transcript contents, paths or credentials were
recorded or stored. Capture hook: `scripts/execution_identity_live_capture.py`.

## 2026-10-23, Claude Code 2.1.306 (real host, `claude -p`)

**Setup.** Two `claude -p --model haiku` processes started concurrently (A: main session only; B: main
session plus one delegated `general-purpose` subagent), each in its own empty temp directory, each
producing a different file (`A-state`; `B-sub-state` from the subagent and `B-main-state` from B itself). The
capture hook was supplied through per-invocation `--settings` with `--setting-sources project,local`, so no
active Claude settings were read for hooks or modified, and no product daemon was involved. No
authentication change, no foreign process touched.

| Case | Result | Evidence |
|---|---|---|
| Two simultaneous sessions have distinct provider-native `session_id` | **OBSERVED** | A and B ids hash differently on every one of their events; reference `cache_key(SESSION)` differs |
| `session_id` present on every hook event | **OBSERVED** | SessionStart, UserPromptSubmit, Pre/PostToolUse, SubagentStart/Stop, Stop, SessionEnd |
| Subagent events carry `agent_id` + `agent_type` | **OBSERVED** | SubagentStart, SubagentStop and the Pre/PostToolUse of the subagent's own Bash call |
| Main-session events carry no `agent_id` | **OBSERVED** | including the main session's `Agent` tool call |
| Subagent events carry the **parent's** `session_id` | **OBSERVED** | all B subagent events share B's session hash; the `(session, agent)` pair names the subagent |
| A parent/child link is exposed | **NOT PRESENT** | no `parent_*` field on any event; `lineage_status` therefore stays `unknown` |
| A `turn_id` is exposed | **NOT PRESENT** | no field with that name on any event |
| `prompt_id` | **OBSERVED, semantics undecided** | constant across all events of one user prompt (main and subagent), differs across prompts and sessions; absent on SessionStart. A prompt-cycle id, not mapped to `turn_id` pending an owner decision |
| `SubagentStop.agent_transcript_path` | OBSERVED (value not recorded) | a separate subagent transcript file exists |
| Session-local state isolation | **OBSERVED** | each session's file contents and hook stream contained only its own state |
| Statusline stdin carries `session_id` | **BLOCKED** | `claude -p` never invoked the configured statusLine command (zero statusline records). An interactive start requires accepting the folder-trust dialog, which writes to the user's `~/.claude.json`; not done under the no-modification constraint |
| Resume / clear / fork / process restart | **NOT RUN** | outside the authorized bound for this run |
| Host-scoped product state (Fornax/Circinus/Libra daemons) and product persistence/cache | **NOT RUN** | product hooks were deliberately excluded to avoid touching live daemons; this is owner-run acceptance (see the product handoffs on HORO-1597) |

Normalizer result on the real payloads: main events scope `session`; subagent events scope `agent`;
`lineage_status` `unknown` throughout.

## What this does and does not prove

- Proves the provider-native facts above for Claude Code 2.1.306 headless mode and that the Host normalizer
  reads real payloads into distinct, correctly scoped identities.
- Does **not** prove any product attributes correctly: each product must show it with its own two-session test
  (acceptance criteria on its handoff ticket).
- Does not cover interactive mode, statusline stdin, or Codex.
