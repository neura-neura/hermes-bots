# Hermes Bot Creator

A native Hermes bot that **creates and edits other Hermes bots through natural conversation**. It manages real Hermes profiles, including bots created manually or outside Bot Creator. It does not ask you to copy generated prompts into an editor.

Examples:

- “Create a concise Python assistant that can look up recent documentation.”
- “Make Coding more technical, but keep its tools and model.”
- “Remove Browser from Research, but keep Web Search.”
- “Make it less formal while preserving its patience.”
- “Duplicate this bot.”
- “Show me what changed.”
- “Undo the last change.”

The included SOUL instructions are written in Spanish; the bot is instructed to respond in the user's language, including English.

## Installation

Prerequisites:

- An existing local Hermes Agent installation under `~/.hermes`, including its source at `~/.hermes/hermes-agent`.
- Hermes Desktop or a local dashboard running with the native profile APIs and localhost session-token handshake.
- Python 3.12 and `lsof` available.
- A working model/provider connection in the default Hermes profile.

From the repository root:

```bash
cd bots/bot-creator
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python install.py
```

Open **Hermes → BOTS → Hermes Bot Creator**. Its canonical profile ID is `bot-creator`. Hermes launches the profile's administrative MCP automatically; no separate daemon or port is created.

The installer creates a native profile by cloning default configuration and skills through Hermes' channel-safe cloning mechanism. It installs its SOUL instructions and a profile-scoped MCP server. Its MCP selection replaces inherited servers for this administrative profile, and its CLI toolsets are limited to clarification and Bot Creator. No other profile is edited.

Keep this directory and its virtual environment in place: the installed MCP configuration references their absolute paths. Re-running the installer refreshes this profile; it does not modify Hermes core. Back up an existing `bot-creator` profile before re-running if you have customized it.

This integration was tested on macOS against a local Hermes installation. Other layouts, remote deployments, authentication configurations, and future API changes are not guaranteed to work without adaptation.

## Architecture

```text
Native Hermes conversation
    → model interprets the request
    → typed administrative MCP tools
    → BotService
    → HermesBotAdapter
    → native Hermes backend
    → real profile files
```

Hermes remains the source of truth. There is no secondary database of bots.

In the inspected Hermes implementation, a bot is a native profile:

- `config.yaml`: model/provider, toolsets, MCP, and runtime settings.
- `SOUL.md`: persistent persona and behavioral instructions.
- `profile.yaml`: description, visible name, and Bot Mode metadata.
- Native profile directories also hold their own sessions, memories, skills, and credentials. Bot Creator does not replace that storage.

`BotSpec` and `BotPatch` are strict schemas. The conversational model interprets intent and rewrites prompts semantically. `BotService` handles execution, validation, preservation, backups, and verification. `HermesBotAdapter` translates those operations into the existing APIs.

Contextual references such as “it,” “also,” and “undo that” remain in the native conversation. There is no shared global target variable across chats.

## Native interfaces used

The adapter discovers local `hermes serve`/`dashboard` processes and listening sockets, obtains the session token through Hermes' localhost handshake, and checks that the profile roster belongs to the expected installation. Ports are not hardcoded. Tokens are neither printed nor persisted.

| Operation | Native interface |
| --- | --- |
| List | `GET /api/profiles` |
| Read configuration | `GET /api/config?profile=<id>` plus native YAML for model fields normalized out of the GET response |
| Read/write instructions | `GET` / `PUT /api/profiles/<id>/soul` |
| Create/duplicate | `POST /api/profiles` with channel-safe cloning |
| Partial configuration edit | `PUT /api/config` with only requested subfields |
| Description | `PUT /api/profiles/<id>/description` |
| Model/provider | `PUT /api/profiles/<id>/model` |
| Tool/model discovery | `GET /api/tools/toolsets`, `GET /api/model/options`, scoped to the target profile |
| Visible name | JSON-RPC `profiles.configure`, using the native Bot Mode metadata CAS |
| Canonical rename | `PATCH /api/profiles/<id>` |
| Delete | `DELETE /api/profiles/<id>` |
| Export/import | Native profile export/import endpoints |

Exact restoration uses Hermes' native atomic file-writing primitives because the REST deep merge cannot remove newly introduced keys or restore absent files. Installer cleanup of inherited MCP configuration uses the same native primitives.

## Create, get, update, and patch

**Create:** validate the specification, canonical ID, visible name, prompt, selected tools, and model. Clone the selected native profile without sessions or messaging channels, apply the requested configuration, and read it back. A partially created profile is reported as incomplete rather than falsely declared ready.

**Get:** read the complete current configuration, SOUL, and metadata. Return redacted values and a revision hash covering the three administrative files. Check that the files did not change during the read.

**Update and patch:** operate on the same profile without deleting or recreating it. Require its current revision, re-read before writing, and pass only explicitly requested fields to the native writers. A style edit does not change tools, model, name, or description.

**Tools:** derive edits from the current effective native selection. `tools_add` and `tools_remove` preserve unrelated selections. `tools_only` explicitly replaces the selection and excludes implicit MCP servers. Validate actual catalog names and configured prerequisites rather than inventing capabilities.

**Prompts:** replace SOUL with a coherent semantic rewrite when requested. The instructions require preserving unrelated rules, resolving contradictions, and avoiding endless appended instructions. Semantic correctness depends on the model; it is not universally guaranteed by deterministic validation.

Successful operations verify the saved fields and compare unrelated effective configuration and metadata. Hermes may normalize YAML/defaults during ordinary saves; rollback restores the original file contents exactly.

## Administrative tools

The MCP exposes 16 tools:

`list_bots`, `get_bot`, `discover_capabilities`, `create_bot`, `update_bot`, `patch_bot`, `validate_bot`, `duplicate_bot`, `rename_bot`, `get_bot_history`, `rollback_bot`, `compare_bots`, `prepare_delete_bot`, `delete_bot`, `export_bot`, and `import_bot`.

Comparison and copying selected settings use real current configurations. A duplicate leaves the original unchanged. Exported archives remain local and may contain private information; do not commit or publish them without reviewing their contents.

## Editable properties and limitations

Supported edits include SOUL instructions, persona, purpose, response style, languages, constraints expressed in SOUL, description, visible name, canonical profile ID, native toolsets, and existing model/provider selections. Multimodal capabilities use real configured toolsets.

This MCP does not expose arbitrary secret editing, new MCP commands, privileged profile roles, avatars, skill management, session/memory databases, or every Hermes setting. It can inspect configuration but must report unsupported edits accurately.

A bot **is** a profile; there is no independent profile property that can be swapped while keeping a separate bot identity. Copy selected settings or create a derivative instead.

Visible-name changes preserve the canonical ID. Native canonical renaming necessarily changes that ID. `default` and `bot-creator` are protected from deletion and canonical renaming. Renaming to an ID with a native deletion tombstone is rejected before mutation because a discovered native behavior can hide that destination; use a fresh ID or a visible-name change.

The `web` toolset groups `web_search` and `web_extract`; Browser is the separate `browser` toolset. This integration does not promise search-only isolation within that bundle. Hermes may add interface/system tools depending on the chat surface; tool selection is not a security sandbox.

The runtime's base system prompt is not replaced. SOUL is its persistent editable persona/instruction layer. Changes to tools, models, or instructions take effect in new target conversations; active cached prompts are not rewritten.

## Backups, history, conflicts, and rollback

Before editing, Bot Creator saves a durable snapshot and checks the supplied revision against fresh state. It serializes its own operations, rechecks state before each step, and verifies saved results. Visible-name metadata also uses Hermes' native per-plugin CAS.

**Concurrency limitation:** configuration/SOUL REST writes do not offer ETag/CAS or a transaction spanning all files. The service lock does not lock external editors. Hash checks detect many conflicts, but a small check/write race remains. Global atomicity is not guaranteed.

If a step fails, the service restores the snapshot only when the current state still matches the state it observed. Otherwise it records `needs_reconciliation` and avoids blindly overwriting external edits. A crash may leave a `prepared` record for explicit review/restoration.

Private history lives in `~/.hermes/bot-creator-history/`: directory permissions 0700 and records/lock 0600. Records include version ID, timestamp, bot ID, operation, before/after snapshots, modified fields, and outcome. Snapshots can contain private configuration; raw snapshot files are not exposed through MCP. There is no automatic retention/purge policy.

History begins with the first managed change, even for externally created bots. Undo supports multiple successive edits. Automatic undo rejects later external edits; explicit version/date restoration should be reviewed first. Date selection requires a timezone-aware ISO timestamp.

Rollback restores the three administrative files, not sessions or the lifecycle of deleted/canonically renamed profiles. Native deletion recovery remains Hermes' responsibility.

Deletion uses a short-lived, single-use ticket bound to the target and revision. The bot must ask and wait for explicit user confirmation. The ticket enforces target/state/expiry; the conversational flow, rather than an independent human-identity verifier, establishes that confirmation came from the user.

## Testing

Run from this bot's directory with Hermes open:

```bash
.venv/bin/python tests/mcp_smoke.py
.venv/bin/python tests/live_crud.py
.venv/bin/python tests/conversation_e2e.py
```

These are live integration tests. They create only `bc-temporary-*` profiles and clean them up through native deletion. They refuse to overwrite existing test IDs. Native recovery archives may remain. Conversation tests use the real model/provider and may consume its quota.

Validated cases include:

- Real CRUD, single-field preservation, and multiple-field edits.
- Removing Browser while preserving Web Search, model, identity, and metadata.
- Native model changes, duplication, copying tools, and renaming.
- Re-reading manual external changes and rejecting stale revisions.
- Multiple undos and exact compensation after a simulated failure following a write.
- A real stdio MCP handshake, typed schemas, and errors without false success.
- Native Desktop WebSocket conversations: creation, contextual edit, tool addition, undo, editing a bot created outside Bot Creator, and preserving a manually added rule.
- Visual inspection in Hermes' normal BOTS editor confirming the saved SOUL.

Generated results remain local under `docs/` and are excluded from version control. Original machine transcripts, profile configuration, credentials, and personal bot inventories are not distributed.

Persistence was checked through new service instances and reopened native state; personal services were not restarted. Export/import tools exist, but not every archive variant, provider, or authentication layout has been tested.
