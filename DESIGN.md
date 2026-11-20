# tadpod design

Agreed 2026-11-20. This is the reference for what v1 is and isn't.

## Who

Technical early adopters who already use an MCP-capable AI app (Claude Code, Claude Desktop,
Cursor). They find tadpod on GitHub and install it with one config line.

## The flow

1. **Install:** `uvx tadpod`, no arguments.
2. **Set up in chat:** the AI explains how to download the LinkedIn export, and the user gives
   it the zip's path. The zip is never attached to the chat, which would send it to the model.
   `import_linkedin` stores a snapshot and returns 1–2 insights plus example questions.
3. **Capture (real time):** an iOS Shortcut scans the person's LinkedIn QR code, appends
   `timestamp URL` to an inbox file in iCloud Drive, and opens LinkedIn immediately. It asks
   no questions.
4. **Queue:** at the start of every tool call, tadpod pulls new inbox lines into `scans` and
   removes them from the file. A scan is *unprocessed* until a context entry is written for
   that person after tadpod received it.
5. **Context (only when asked):** "process my queue" → the AI shows each scan's time and asks
   for free-form context, saved with `add_context`. The AI doesn't bring up the queue unprompted.
6. **Ask anything:** read-only `query(sql)`.
7. **Refresh occasionally:** re-import the export (about quarterly) for message history and
   role changes. Each import is a dated snapshot; the newest is current.

## Decisions

| Area | Decision |
|---|---|
| Tools | `import_linkedin`, `add_person` (manual add; queued if no context), `add_context` (existing people only), `query` (read-only SELECT) |
| Context | Dated free-form entries, for anyone. `date` defaults to today; `noted_at` records when it was written. |
| People | One row per person, keyed by LinkedIn slug when known. Name-only people are linked to a connection on import when exactly one name matches. |
| Imports | Snapshots kept (`snapshot_connections`). Messages and invitations replaced by the newest import that includes them. |
| Scan payload | URL + timestamp only. No location, no prompts. |
| Storage | `~/.tadpod/tadpod.db` on the laptop. Only the inbox (URLs + times) passes through iCloud. |
| Platforms | iPhone first. The inbox format is documented so others can build Android capture. Chat-only adding works everywhere. |
| Install | PyPI + `uvx`. A Claude Desktop extension (`.mcpb`) comes later. |
| Name | tadpod. "LinkedIn" never appears in the product name (trademark). |

## Not in v1

- **Pods** (v2): invite-only groups. A pod is a shared folder, and membership is a key.
  Members publish encrypted, signed pod cards (hidden / anonymous / named per contact). Intro
  requests are encrypted to the connector, who approves before any name is revealed. Removal
  rotates the key. There is no central server.
- Nudges and reminders, editing or deleting entries, Android capture, a desktop app, and
  pulling profile data from LinkedIn (not allowed by its terms).
