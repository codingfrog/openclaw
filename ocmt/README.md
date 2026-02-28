# ocmt

Multi-tenant LLM service with Markdown memory. Each tenant gets isolated
conversations backed by persistent Markdown files and full-text search.
LLM execution goes through your local Claude Code CLI — no API keys required
for the default setup.

## Prerequisites

- Python 3.11+
- [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) installed
  and authenticated (`claude` must be on your PATH)

## Install

```bash
cd ocmt
pip install -e ".[dev]"
```

## Quick start

### 1. Create a tenant

A tenant is an isolated workspace — its own memory files, sessions, and
conversation history. Think of it as a separate user/project.

```bash
ocmt tenant-create "Alice" alice
```

Output:

```
Created tenant: Alice (alice)
Tenant ID: a1b2c3d4-...
API Key: ocmt_Abc123...
Save this API key — it won't be shown again.
```

Save the API key. You need it for API/WebSocket access. The CLI uses the
tenant slug directly.

### 2. Chat via CLI

```bash
ocmt chat "What can you help me with?" --tenant alice
```

The first message creates a session. Follow-up messages resume the same
session automatically (keyed by tenant + channel + user).

```bash
ocmt chat "Remember that I prefer Python over JavaScript" --tenant alice
ocmt chat "What language do I prefer?" --tenant alice
```

The agent sees memory from prior conversations — it knows you said Python.

### 3. Start the API server

```bash
ocmt serve
```

Server starts on `http://0.0.0.0:8000`. API docs at `http://localhost:8000/docs`.

### 4. Chat via REST API

```bash
# Send a message
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer ocmt_Abc123..." \
  -H "Content-Type: application/json" \
  -d '{"message": "Hello, remember my name is Alice"}'

# Follow-up (session resumes automatically)
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer ocmt_Abc123..." \
  -H "Content-Type: application/json" \
  -d '{"message": "What is my name?"}'
```

### 5. Chat via WebSocket

Connect to `ws://localhost:8000/ws/chat` and send JSON frames:

```json
{"api_key": "ocmt_Abc123...", "message": "Hello from WebSocket"}
```

Response:

```json
{"type": "response", "text": "...", "session_key": "agent:main:user-", "model": "sonnet"}
```

## How conversations work

### Tenant-level isolation

Isolation is at the **tenant** level. Each tenant gets its own:
- Memory files (`MEMORY.md`, `memory/*.md`)
- Conversation sessions and transcripts
- FTS5 search index
- API key

All channels (CLI, REST API, WebSocket, Telegram, etc.) share the same
memory and session state within a tenant. A user who starts a conversation
via CLI can continue it via the API — context carries over seamlessly.

### Session routing

Every conversation is identified by a **session key** built from two
components:

```
agent:{agentId}:user-{userId}
```

- **agent** — which agent personality to use (default: `main`)
- **user** — who is talking

Channel is **not** part of the session key. The same user always lands
in the same session regardless of which channel they use. Channel is
recorded in transcript entries as metadata for audit purposes.

### Multi-user conversations

Multiple users can talk to the same tenant, each with their own session:

```bash
# User A's conversation (via CLI)
ocmt chat "I'm working on the frontend" --tenant alice --user user-a

# User B's conversation (separate session, separate context)
ocmt chat "I'm working on the backend" --tenant alice --user user-b

# User A continues via a different channel (picks up the same session)
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer ocmt_Abc123..." \
  -H "Content-Type: application/json" \
  -d '{"message": "What am I working on?", "user_id": "user-a"}'
# -> "You mentioned you're working on the frontend."
```

The cross-channel continuity works because the session key is
`agent:main:user-user-a` regardless of whether the message came from
CLI, API, or WebSocket.

Via the API, set `user_id` in the request body:

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Authorization: Bearer ocmt_Abc123..." \
  -H "Content-Type: application/json" \
  -d '{"message": "Deploy the staging server", "user_id": "ops-team"}'
```

### Multiple tenants

Each tenant is fully isolated — separate memory, separate sessions,
separate API key:

```bash
ocmt tenant-create "Project Alpha" alpha
ocmt tenant-create "Project Beta" beta

# These are completely independent conversations
ocmt chat "We use PostgreSQL" --tenant alpha
ocmt chat "We use MongoDB" --tenant beta
```

## Memory system

Memory is the core feature. It lets the agent remember things across
conversations without you having to repeat context.

### How it works

1. **Markdown files are the source of truth.** Everything is stored as
   plain `.md` files on disk — human-readable, editable, version-controllable.

2. **Two types of memory files:**
   - `memory/YYYY-MM-DD.md` — Daily logs. Append-only. One per day.
   - `MEMORY.md` — Long-term curated memory. The agent updates this with
     important facts, preferences, and decisions.

3. **SQLite FTS5 index** — The markdown files are chunked and indexed into
   a per-tenant SQLite database for fast full-text search. The database is
   just an index; delete it and it rebuilds from the files.

4. **Bootstrap context** — At the start of each conversation turn, the agent
   automatically loads today's log + yesterday's log + MEMORY.md into its
   system prompt.

### Memory search

Search a tenant's memory index:

```bash
ocmt memory-search "database preferences" --tenant alice
```

Via API:

```bash
curl -X POST http://localhost:8000/api/v1/memory/search \
  -H "Authorization: Bearer ocmt_Abc123..." \
  -H "Content-Type: application/json" \
  -d '{"query": "database preferences"}'
```

### Write to memory directly

```bash
curl -X POST http://localhost:8000/api/v1/memory/write \
  -H "Authorization: Bearer ocmt_Abc123..." \
  -H "Content-Type: application/json" \
  -d '{"content": "## Project setup\nUsing FastAPI with PostgreSQL."}'
```

### Inspect memory files

Memory files live at `data/tenants/{slug}/workspace/memory/` and
`data/tenants/{slug}/workspace/MEMORY.md`. You can read and edit them
directly — they're just Markdown.

```bash
cat data/tenants/alice/workspace/MEMORY.md
cat data/tenants/alice/workspace/memory/2026-02-28.md
```

### Memory index status

```bash
ocmt memory-status --tenant alice
```

### Memory flush

When a session accumulates enough tokens (approaching context limits),
the agent is prompted to write a summary of important information to memory
files before the session is compacted. This prevents knowledge loss during
long conversations.

## Configuration

All settings live in `config.yaml`. The defaults work out of the box.

```yaml
database:
  path: "ocmt.sqlite"              # Global tenant/session database

tenant_workspace_root: "data/tenants" # Where tenant files live

agents:
  default_model: "sonnet"           # Primary model
  fallback_models:                  # Tried in order if primary fails
    - "haiku"
  cli:
    command: "claude"               # CLI binary name
    timeout_ms: 120000              # 2 minute timeout per request
    api_key: ""                     # Optional: explicit API key override

memory:
  chunk_tokens: 400                 # Tokens per search chunk
  chunk_overlap: 80                 # Overlap between chunks
  max_results: 6                    # Max search results returned
  min_score: 0.35                   # Minimum relevance score

api:
  host: "0.0.0.0"
  port: 8000
```

### Per-tenant API keys

If you want different tenants to use different Anthropic billing, pass an
API key at tenant creation:

```bash
ocmt tenant-create "Client X" client-x --anthropic-key sk-ant-...
```

When set, that tenant's CLI subprocess will use the provided key instead
of the server operator's default auth.

## Workspace layout

Each tenant gets an isolated directory tree:

```
data/tenants/
  alice/
    workspace/
      memory/
        2026-02-28.md          # Today's daily log
        2026-02-27.md          # Yesterday's log
      MEMORY.md                # Long-term memory
    sessions/
      agent_main_user_cli_user.jsonl       # Transcript (shared across channels)
    memory.sqlite              # Per-tenant FTS5 index
  beta/
    workspace/
      ...
```

The global database (`ocmt.sqlite`) stores tenant records and session
metadata. Per-tenant SQLite databases store the full-text search index.

## API reference

All endpoints require `Authorization: Bearer <api_key>`. Tenant admin
endpoints (`POST /api/v1/tenants`, `GET /api/v1/tenants`) also require
an `X-Admin-Key` header (set via `OCMT_ADMIN_KEY` env var).

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/v1/chat` | Send a message, get a response |
| POST | `/api/v1/memory/search` | Search tenant memory |
| POST | `/api/v1/memory/write` | Write to daily memory log |
| GET | `/api/v1/memory/daily/{date}` | Read a daily log |
| GET | `/api/v1/sessions` | List tenant sessions |
| DELETE | `/api/v1/sessions/{key}` | Delete a session |
| POST | `/api/v1/tenants` | Create a tenant (admin, X-Admin-Key) |
| GET | `/api/v1/tenants` | List all tenants (admin, X-Admin-Key) |
| WS | `/ws/chat` | WebSocket chat |

### POST /api/v1/chat

```json
{
  "message": "Hello",
  "user_id": "user-123",
  "agent_id": "main",
  "channel": "api",
  "model": "sonnet"
}
```

Only `message` is required. All other fields have defaults. `channel` is
recorded in the transcript for audit but does not affect session routing.

Response:

```json
{
  "text": "Hello! How can I help?",
  "session_key": "agent:main:user-user-123",
  "model": "sonnet",
  "usage": {"input_tokens": 150, "output_tokens": 30, "total": 180}
}
```

## Testing

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

57 tests covering tenants, memory, sessions, LLM runner, and search.

## Architecture

```
ocmt/
  tenants/         Tenant CRUD, workspace isolation, API key auth
  memory/          Markdown store, chunker, FTS5 indexer, hybrid search
  llm/             Claude Code CLI subprocess runner, fallback chains
  sessions/        Session keys, JSONL transcripts, compaction
  agents/          Orchestrator tying memory + LLM + sessions together
  api/             FastAPI routes, WebSocket handler, bearer auth
  cli/             Typer CLI commands
  channels/        Channel adapter base class (Telegram/Discord/Slack)
  embeddings/      Embedding provider base class (future vector search)
```

The agent orchestrator (`agents/runner.py`) is the central piece. Each
call to `AgentRunner.run()` executes a full turn:

1. Resolve tenant workspace from API key
2. Get or create session (keyed by tenant + agent + user; channel-independent)
3. Check if session needs compaction (token limit approaching)
4. Run memory flush if needed (save important context before compaction)
5. Load bootstrap memory (today + yesterday + MEMORY.md)
6. Build system prompt with memory context
7. Execute via Claude Code CLI subprocess (with model fallback)
8. Record transcript to JSONL (with channel metadata)
9. Update session state (tokens, CLI session ID)
