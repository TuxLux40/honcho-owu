"""
title: Honcho Memory
author: oliver
version: 0.6.1
description: Automatic long-term memory via MANAGED Honcho cloud (app.honcho.dev).
    Injects the user's representation before generation (inlet) and ingests each
    turn afterwards (outlet). Emits OWU status events so memory activity is visible
    in the chat (collapsible status chips), including a loud error chip if the
    honcho-ai package or API key is missing.
requirements: honcho-ai>=2.1

Portable by default: human peer id is derived per-OWU-account from __user__ (id/email),
not hardcoded. Set peer_id_overrides to pin a specific OWU account to an existing Honcho
peer (e.g. "ai@deroliver.me:oliver"). Set excluded_model_ids to skip specific models (e.g.
an internal agent) while staying global. Type: filter -> set global to apply on every model.

Requires the `honcho-ai` package. OWU normally auto-installs it from the requirements line
above when this function loads. If your instance runs with OFFLINE_MODE=true or has pip
disabled, that auto-install is skipped — install manually into OWU's venv:
pip install 'honcho-ai>=2.1'. If missing, every recall/store now shows a visible
"Honcho error: ..." chip in chat instead of silently doing nothing.

Note: OWU's requirements-frontmatter installer (open_webui/utils/plugin.py) naively splits
this line on commas, so a single spec like ">=2.1,<3" gets torn into two invalid pip args
and fails. Keep this line comma-free (single lower-bound spec, or an exact == pin).
"""

from pydantic import BaseModel, Field
from typing import Optional, Callable, Awaitable
import re
import asyncio


def _sanitize(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", raw or "") or "default"


async def _emit(emitter, status: str, done: bool = False, description: str = ""):
    if emitter is None:
        return
    try:
        payload = {
            "type": "status",
            "data": {
                "status": "complete" if done else "in_progress",
                "description": description or status,
            },
        }
        result = emitter(payload)
        if asyncio.iscoroutine(result):
            await result
    except Exception:
        pass


class Filter:
    class Valves(BaseModel):
        honcho_api_key: str = Field(
            default="", description="Managed Honcho API key (app.honcho.dev)."
        )
        honcho_environment: str = Field(
            default="production",
            description="Managed env: production | demo. Ignored if base_url set.",
        )
        honcho_base_url: str = Field(
            default="",
            description="Self-hosted override. Leave empty for managed cloud.",
        )
        workspace_id: str = Field(default="main")
        user_peer_id: str = Field(
            default="user",
            description="Fallback human peer id, used only when per_user_peers is off or __user__ is unavailable (e.g. raw API calls).",
        )
        assistant_peer_id: str = Field(
            default="openwebui", description="Interface/observer peer"
        )
        session_id: str = Field(default="openwebui")
        per_user_peers: bool = Field(
            default=True,
            description="Derive the human peer id from OWU's __user__ (id/email) instead of the fixed user_peer_id valve. Prevents cross-user memory bleed.",
        )
        peer_id_overrides: str = Field(
            default="",
            description="Comma-separated owu_id_or_email:peer_id pairs to pin specific OWU accounts to an existing Honcho peer, e.g. 'ai@deroliver.me:oliver'. Checked before deriving from __user__.",
        )
        max_context_chars: int = Field(default=6000)
        inject_context: bool = Field(default=True)
        auto_ingest: bool = Field(default=True)
        show_status: bool = Field(
            default=True,
            description="Show memory recall/ingest status chips in chat.",
        )
        show_context_preview: bool = Field(
            default=True,
            description="Include first 300 chars of recalled context in status chip.",
        )
        excluded_model_ids: str = Field(
            default="",
            description="Comma-separated model ids to skip (e.g. the Hermes agent's model id), even when this filter is global.",
        )

    def __init__(self):
        self.valves = self.Valves()
        self.type = "filter"
        self._client = None
        self._last_error = None

    def _excluded(self, body: dict) -> bool:
        excluded = {m.strip() for m in self.valves.excluded_model_ids.split(",") if m.strip()}
        return bool(excluded) and body.get("model") in excluded

    def _overrides(self) -> dict:
        out = {}
        for pair in self.valves.peer_id_overrides.split(","):
            if ":" not in pair:
                continue
            key, _, peer_id = pair.partition(":")
            key, peer_id = key.strip(), peer_id.strip()
            if key and peer_id:
                out[key] = peer_id
        return out

    def _user_peer_id(self, __user__: Optional[dict]) -> str:
        if self.valves.per_user_peers and __user__:
            uid, email = __user__.get("id"), __user__.get("email")
            overrides = self._overrides()
            if uid and uid in overrides:
                return _sanitize(overrides[uid])
            if email and email in overrides:
                return _sanitize(overrides[email])
            ident = uid or email
            if ident:
                return _sanitize(str(ident))
        return self.valves.user_peer_id

    # ---- managed honcho client (cached) -------------------------------- #
    def _honcho(self):
        if self._client is not None:
            return self._client
        v = self.valves
        if not v.honcho_api_key:
            raise RuntimeError("honcho_api_key valve is empty")
        try:
            from honcho import Honcho
        except ImportError:
            raise RuntimeError(
                "honcho-ai package not installed. OWU normally auto-installs it from this "
                "function's requirements line on load; if OFFLINE_MODE or pip is disabled "
                "on your instance, install it manually into OWU's venv: "
                "pip install 'honcho-ai>=2.1'"
            )

        kwargs = dict(api_key=v.honcho_api_key, workspace_id=v.workspace_id)
        if v.honcho_base_url:
            kwargs["base_url"] = v.honcho_base_url
        else:
            kwargs["environment"] = v.honcho_environment
        self._client = Honcho(**kwargs)
        return self._client

    def _peer(self, name: str, observe_me: bool):
        from honcho.api_types import PeerConfig

        return self._honcho().peer(
            _sanitize(name), configuration=PeerConfig(observe_me=observe_me)
        )

    # ---- retrieval ----------------------------------------------------- #
    def _get_context(self, __user__: Optional[dict] = None) -> str:
        self._last_error = None
        try:
            import re
            user_peer_id = self._user_peer_id(__user__)
            peer = self._peer(user_peer_id, True)
            ctx = peer.context(max_conclusions=25, include_most_frequent=True)

            parts = []

            peer_card = getattr(ctx, "peer_card", None)
            if peer_card:
                parts.append("Profile:\n" + "\n".join(f"- {item}" for item in peer_card))

            rep = getattr(ctx, "representation", None)
            if isinstance(rep, str) and rep.strip():
                lines = [l for l in rep.split("\n") if l.strip() and not l.startswith("#")]
                lines = [re.sub(r"^\[.*?\]\s*", "", l) for l in lines[:10]]
                if lines:
                    parts.append("Recent observations:\n" + "\n".join(f"- {l}" for l in lines))

            if not parts:
                return ""

            text = f"[Honcho Memory for {user_peer_id}]\n\n" + "\n\n".join(parts)
            if len(text) > self.valves.max_context_chars:
                text = text[: self.valves.max_context_chars] + "\n...[truncated]"
            return text
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"
            print(f"[Honcho] context error: {self._last_error}")
            return ""

    # ---- ingestion ----------------------------------------------------- #
    def _ingest(self, user_text: str, assistant_text: str, __user__: Optional[dict] = None):
        self._last_error = None
        try:
            from honcho.api_types import SessionPeerConfig

            user_peer_id = self._user_peer_id(__user__)
            session_id = user_peer_id if self.valves.per_user_peers else self.valves.session_id
            h = self._honcho()
            session = h.session(_sanitize(session_id))
            me = self._peer(user_peer_id, True)
            agent = self._peer(self.valves.assistant_peer_id, False)
            session.add_peers(
                [
                    (me, SessionPeerConfig(observe_me=True, observe_others=False)),
                    (agent, SessionPeerConfig(observe_me=False, observe_others=True)),
                ]
            )
            msgs = []
            if user_text and user_text.strip():
                msgs.append(me.message(user_text))
            if assistant_text and assistant_text.strip():
                msgs.append(agent.message(assistant_text))
            if msgs:
                session.add_messages(msgs)
            return len(msgs)
        except Exception as e:
            self._last_error = f"{type(e).__name__}: {e}"
            print(f"[Honcho] ingest error: {self._last_error}")
            return 0

    # ---- OWU hooks ----------------------------------------------------- #
    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> dict:
        if not self.valves.inject_context or self._excluded(body):
            return body
        try:
            messages = body.get("messages", [])
            if not messages:
                return body

            emitter = __event_emitter__ if self.valves.show_status else None
            await _emit(emitter, "Recalling memory…")

            context = self._get_context(__user__)
            if context:
                preview = ""
                if self.valves.show_context_preview:
                    preview = " — " + context[:300].replace("\n", " ")
                    if len(context) > 300:
                        preview += "…"
                await _emit(
                    emitter,
                    f"Memory recalled{preview}",
                    done=True,
                )
                injection = f"\n\n{context}\n"
                sys_msg = next(
                    (m for m in messages if m.get("role") == "system"), None
                )
                if sys_msg:
                    sys_msg["content"] = (sys_msg.get("content", "") or "") + injection
                else:
                    messages.insert(
                        0, {"role": "system", "content": injection.strip()}
                    )
            elif self._last_error:
                await _emit(emitter, f"Honcho error: {self._last_error}", done=True)
            else:
                await _emit(emitter, "No memory found", done=True)
        except Exception as e:
            print(f"[Honcho] inlet error: {type(e).__name__}: {e}")
        return body

    async def outlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> dict:
        if not self.valves.auto_ingest or self._excluded(body):
            return body
        try:
            messages = body.get("messages", [])
            if not messages or messages[-1].get("role") != "assistant":
                return body

            emitter = __event_emitter__ if self.valves.show_status else None
            await _emit(emitter, "Storing to memory…")

            assistant = messages[-1]
            user = next(
                (m for m in reversed(messages[:-1]) if m.get("role") == "user"), None
            )
            count = self._ingest(
                (user or {}).get("content", ""), assistant.get("content", ""), __user__
            )
            if count == 0 and self._last_error:
                await _emit(emitter, f"Honcho error: {self._last_error}", done=True)
            else:
                await _emit(
                    emitter,
                    f"Stored {count} message(s) to Honcho",
                    done=True,
                )
        except Exception as e:
            print(f"[Honcho] outlet error: {type(e).__name__}: {e}")
        return body
