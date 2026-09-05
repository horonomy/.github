# Jira delivery invariants

Detailed rule text for `governance/README.md`'s Company layer.

## Project and hierarchy

Track work in the Horonom Jira project (**HORO**). Hierarchy is Epic →
Story → Subtask, with roughly one Subtask per commit and a `Verify …`
subtask per Story where the story's scope warrants a distinct verification
step.

## Ticket-to-repo mapping

Set the Component on every ticket to the GitHub repo it targets. A
cross-repo ticket either gets one component per affected repo or is split
into per-repo sub-tickets linked to a parent — pick whichever keeps each
ticket's diff traceable to a single repo's PR.

## Truthful status

Never transition a ticket to Done until its acceptance criteria are
genuinely satisfied by merged evidence — a status field is not itself
evidence. When a wave of work genuinely begins, add a short "Starting work"
comment naming what's about to happen, so anyone reading the ticket later
can see when real work started versus when the ticket was merely opened.

## External-repository restriction

Do not open an issue, PR, or comment in a third-party (non-Horonom-owned)
repository without explicit owner approval. Read-only research against an
upstream repository is fine.
