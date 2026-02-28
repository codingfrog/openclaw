"""WebSocket handler for streaming chat responses."""

from __future__ import annotations

import json
import logging

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

# Set by app startup
_agent_runner = None
_tenant_manager = None


def set_ws_dependencies(agent_runner, tenant_manager):
    global _agent_runner, _tenant_manager
    _agent_runner = agent_runner
    _tenant_manager = tenant_manager


async def chat_websocket(websocket: WebSocket):
    """WebSocket endpoint for streaming chat.

    Protocol:
    1. Client sends: {"api_key": "...", "message": "...", "user_id": "..."}
    2. Server authenticates and runs agent
    3. Server sends: {"type": "response", "text": "..."}
    4. Server sends: {"type": "done", "session_key": "..."}
    """
    await websocket.accept()

    try:
        while True:
            data = await websocket.receive_text()

            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "Invalid JSON"})
                continue

            api_key = msg.get("api_key", "")
            tenant = _tenant_manager.authenticate(api_key)
            if not tenant:
                await websocket.send_json({"type": "error", "detail": "Invalid API key"})
                continue

            message = msg.get("message", "").strip()
            if not message:
                await websocket.send_json({"type": "error", "detail": "Empty message"})
                continue
            if len(message) > 100_000:
                await websocket.send_json({"type": "error", "detail": "Message too long"})
                continue

            user_id = str(msg.get("user_id", ""))[:256]
            agent_id = str(msg.get("agent_id", "main"))[:64]
            channel = str(msg.get("channel", "ws"))[:64]

            try:
                result = await _agent_runner.run(
                    tenant_id=tenant.id,
                    prompt=message,
                    user_id=user_id,
                    agent_id=agent_id,
                    channel=channel,
                )

                await websocket.send_json({
                    "type": "response",
                    "text": result.text,
                    "session_key": result.session_key,
                    "model": result.model,
                })

            except Exception:
                logger.exception("WebSocket agent run failed")
                # Never expose raw exception text — may contain paths,
                # API keys, or internal state.
                await websocket.send_json({
                    "type": "error",
                    "detail": "Internal error processing request",
                })

    except WebSocketDisconnect:
        logger.debug("WebSocket client disconnected")
