# Honcho Memory for Open WebUI

[![Open WebUI Community](https://img.shields.io/badge/Open%20WebUI-Community%20listing-blue)](https://openwebui.com/posts/honcho_memory_for_owu_d51f2547)
[![Version](https://img.shields.io/badge/version-0.6.2-green)](https://github.com/TuxLux40/honcho-owu/releases/tag/v0.6.2)

Open WebUI Function (`filter` type) that gives any model automatic long-term memory via
[Honcho](https://honcho.dev) — the managed cloud, not a self-hosted instance you have to run
yourself.

**Install from the [Open WebUI community listing](https://openwebui.com/posts/honcho_memory_for_owu_d51f2547)**
(one-click import) or clone this repo and import `honcho-owu.py` manually.

Before generation, it recalls what Honcho knows about the current user and injects it as
context (`inlet`). After generation, it stores the exchange back to Honcho (`outlet`). Memory
activity shows up in chat as collapsible status chips ("Memory recalled...", "Stored 1
message(s) to Honcho"), including a loud error chip if something's misconfigured instead of
silently doing nothing.

## Quickstart

1. Get a free API key at [app.honcho.dev](https://app.honcho.dev) (Settings > API Keys).
2. In Open WebUI: Admin Panel > Functions > import the function — either from the
   [community listing](https://openwebui.com/posts/honcho_memory_for_owu_d51f2547) (**Get**)
   or by importing `honcho-owu.py` from this repo.
3. Open the function's Valves and paste your key into `honcho_api_key`.
4. Set the function's Type to `filter` and enable it (globally, or per-model).

That's it — memory starts flowing on your next message. Everything else has a working default.

If `honcho_api_key` is empty, every chat shows a `Honcho error: honcho_api_key valve is empty`
status chip instead of silently doing nothing.

## How peers are identified

Honcho organizes memory by *workspace* and *peer*. By default this function derives a peer ID
per OWU account automatically (from `__user__`'s id or email) — each person who chats gets
their own isolated Honcho memory, with no cross-user bleed, and no manual setup required.

Two valves let you override that:

- **`per_user_peers`** (default `true`) — turn off to use a single fixed `user_peer_id` for
  everyone instead (e.g. single-user instances).
- **`peer_id_overrides`** — comma-separated `owu_id_or_email:peer_id` pairs to pin specific OWU
  accounts to an existing Honcho peer, e.g. `ai@deroliver.me:oliver`. Useful if you already have
  a Honcho peer from another integration (CLI agent, etc.) and want OWU to write into the same
  one instead of creating a new one.

## Valves

| Valve | Default | Purpose |
|---|---|---|
| `honcho_api_key` | *(empty, required)* | Your Honcho API key. |
| `honcho_environment` | `production` | Managed env (`production`/`demo`). Ignored if `honcho_base_url` is set. |
| `honcho_base_url` | *(empty)* | Set to point at a self-hosted Honcho instance instead of the managed cloud. |
| `workspace_id` | `main` | Honcho workspace to use. |
| `user_peer_id` | `user` | Fallback human peer id, used only when `per_user_peers` is off or `__user__` is unavailable. |
| `assistant_peer_id` | `openwebui` | Peer id representing the assistant/interface itself. |
| `session_id` | `openwebui` | Fixed session id, used only when `per_user_peers` is off. |
| `per_user_peers` | `true` | Derive peer id from `__user__` instead of the fixed `user_peer_id`. |
| `peer_id_overrides` | *(empty)* | Pin specific OWU accounts to existing Honcho peers. |
| `max_context_chars` | `6000` | Truncate injected context above this length. |
| `inject_context` | `true` | Recall memory before generation. |
| `auto_ingest` | `true` | Store each exchange after generation. |
| `show_status` | `true` | Show status chips in chat. |
| `show_context_preview` | `true` | Include a short preview of recalled context in the status chip. |
| `excluded_model_ids` | *(empty)* | Comma-separated model ids to skip even when this filter is global (e.g. an internal agent model). |

## Requirements

Installs `honcho-ai>=2.1` automatically from the function's `requirements:` frontmatter when
loaded. If your instance has `OFFLINE_MODE=true` or pip disabled, install manually into Open
WebUI's venv:

```
pip install 'honcho-ai>=2.1'
```

## Links

- [Open WebUI community listing](https://openwebui.com/posts/honcho_memory_for_owu_d51f2547) — one-click import
- [GitHub releases](https://github.com/TuxLux40/honcho-owu/releases) — versioned snapshots

## Notes

- Type must be `filter`, not `tool` or `pipe`.
- Set Global to apply on every model, then use `excluded_model_ids` to skip specific ones
  (e.g. a model that already has its own memory layer) rather than manually re-enabling the
  filter per-model every time you add a new one.
- The `requirements:` line intentionally avoids commas in the version spec — Open WebUI's
  installer naively splits on `,`, so `honcho-ai>=2.1,<3` breaks into two invalid pip args.
  Keep it a single comma-free spec.
