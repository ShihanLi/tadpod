# tadpod 🐸

**Your people, on your machine.**

tadpod is a local [MCP](https://modelcontextprotocol.io) server that keeps a private record
of the people you know, and lets you ask your AI app about them.

- **Import** your LinkedIn data export. Each import is kept, so you can see who changed jobs.
- **Scan** someone's LinkedIn QR code at an event with an iPhone shortcut. LinkedIn opens
  instantly so you can connect, and tadpod queues them for you.
- **Add context** later, in plain words: where you met, what you talked about, anything.
- **Ask anything:** "Who did I talk to a lot but haven't in a year?", "What did Rosa and I
  last discuss?", "Who did I meet this month?"

Everything is stored in one SQLite file on your laptop. Pods (invite-only groups that can
search each other's networks without a central server) are next. See [DESIGN.md](DESIGN.md).

## Install

Requires [uv](https://docs.astral.sh/uv/).

Claude Code:
```sh
claude mcp add tadpod -- uvx tadpod
```

Claude Desktop (`claude_desktop_config.json`):
```json
{ "mcpServers": { "tadpod": { "command": "uvx", "args": ["tadpod"] } } }
```

Then ask your AI app: **"Set up tadpod."** It walks you through downloading your LinkedIn
export and importing it.

## Phone capture (iPhone)

Set up the "Add to tadpod" shortcut once: [docs/ios-shortcut.md](docs/ios-shortcut.md).
Scans land in an inbox file in iCloud Drive, which tadpod pulls in automatically. When you're
ready, tell your AI app **"process my tadpod queue."**

## Adding people by hand

Any time, on your laptop: "Add Jane Kim, linkedin.com/in/janekim, we met at the climbing gym."
A link, a name, or both works. Add context now, or leave it for your queue. Anything that
appends lines to the inbox file works too (format below).

## Tools

| Tool | What it does |
|---|---|
| `import_linkedin(path)` | Import an export zip or folder from a local path. Returns insights and, on re-imports, what changed. |
| `add_person(url, name, context)` | Add someone by hand (URL, name, or both). Without context they're queued to fill in later. |
| `add_context(person, text)` | Save a dated free-form note about someone already in tadpod (URL, name, or id). |
| `query(sql)` | Read-only SQL over everything: people, context, scans, the `unprocessed` queue, connections, snapshots, messages, invitations. |

## Privacy

- **Local.** One SQLite file in `~/.tadpod/`. tadpod makes no network calls.
- **The only thing in the cloud is the phone inbox:** profile URLs and timestamps, cleared
  after each pull.
- **Your AI app sees what tools return.** Query results, including any message text or
  context you ask about, go to that app's model provider. Use a local-model MCP client if
  that's a problem.
- **No LinkedIn automation.** tadpod only reads the export you download yourself.

## Inbox format

One scan per line, in any `.txt` file in the inbox folder:

```
2026-11-18T19:42:00-05:00 https://www.linkedin.com/in/janekim
```

An ISO 8601 timestamp (optional; the file's time is used if missing), a space, then the
profile URL. Lines without a URL are left in the file untouched.

The default inbox folders are `iCloud Drive/Shortcuts/tadpod/` and `iCloud Drive/tadpod/`.

## Configuration

| Env var / flag | Default |
|---|---|
| `TADPOD_HOME` / `--home` | `~/.tadpod` |
| `TADPOD_INBOX` / `--inbox` | the iCloud Drive folders above |

## Development

```sh
uv sync
uv run pytest
```

Tests use fake fixture data only. Never commit real data: `.gitignore` blocks CSV, zip and
database files outside `tests/fixtures/`.

## License

MIT
