"""
title: Honcho Memory
author: oliver
version: 0.4.0
description: Automatic long-term memory via MANAGED Honcho cloud (app.honcho.dev).
    Injects the user's representation before generation (inlet) and ingests each
    turn afterwards (outlet). Emits OWU status events so memory activity is visible
    in the chat (collapsible status chips).
requirements: honcho-ai>=2.1,<3

Single-user homelab: human peer = "oliver", interface/observer peer = "openwebui".
Type: filter -> set global to apply on every model.
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
        user_peer_id: str = Field(default="oliver", description="Human peer")
        assistant_peer_id: str = Field(
            default="openwebui", description="Interface/observer peer"
        )
        session_id: str = Field(default="openwebui")
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

    def __init__(self):
        self.valves = self.Valves()
        self.type = "filter"
        self._client = None

    # ---- managed honcho client (cached) -------------------------------- #
    def _honcho(self):
        if self._client is not None:
            return self._client
        v = self.valves
        if not v.honcho_api_key:
            raise RuntimeError("honcho_api_key valve is empty")
        from honcho import Honcho

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
    def _get_context(self) -> str:
        try:
            import re
            peer = self._peer(self.valves.user_peer_id, True)
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

            text = f"[Honcho Memory for {self.valves.user_peer_id}]\n\n" + "\n\n".join(parts)
            if len(text) > self.valves.max_context_chars:
                text = text[: self.valves.max_context_chars] + "\n...[truncated]"
            return text
        except Exception as e:
            print(f"[Honcho] context error: {type(e).__name__}: {e}")
            return ""

    # ---- ingestion ----------------------------------------------------- #
    def _ingest(self, user_text: str, assistant_text: str):
        try:
            from honcho.api_types import SessionPeerConfig

            h = self._honcho()
            session = h.session(_sanitize(self.valves.session_id))
            me = self._peer(self.valves.user_peer_id, True)
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
            print(f"[Honcho] ingest error: {type(e).__name__}: {e}")
            return 0

    # ---- OWU hooks ----------------------------------------------------- #
    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __event_emitter__: Optional[Callable[[dict], Awaitable[None]]] = None,
    ) -> dict:
        if not self.valves.inject_context:
            return body
        try:
            messages = body.get("messages", [])
            if not messages:
                return body

            emitter = __event_emitter__ if self.valves.show_status else None
            await _emit(emitter, "Recalling memory…")

            context = self._get_context()
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
        if not self.valves.auto_ingest:
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
                (user or {}).get("content", ""), assistant.get("content", "")
            )
            await _emit(
                emitter,
                f"Stored {count} message(s) to Honcho",
                done=True,
            )
        except Exception as e:
            print(f"[Honcho] outlet error: {type(e).__name__}: {e}")
        return body