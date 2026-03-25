"""ARCHIVE / REFERENCE ONLY — not imported by the app.

Older ``uagents-core`` ``/av/chat`` handled ``message/send`` by reading ``result['parts']``,
while ``a2a-sdk`` often returns a Task with text under ``result['artifacts'][].parts[]``.
That caused ``Failed to parse A2A agent response: 'parts'`` until either:

- **Preferred:** emit the final answer with ``a2a.utils.new_agent_text_message`` (see
  ``executor.py`` and Fetch's innovation-lab-examples orchestrator), or
- **This file (optional):** monkey-patch ``AgentverseA2AStarletteApplication._chat`` to
  extract text from both shapes. To use it manually, after ``agentverse_sdk.init(...)`` call::

    from simple_a2a_agent.agentverse_task_result_patch import apply_agentverse_a2a_chat_patch
    apply_agentverse_a2a_chat_patch()

Upstream fix belongs in ``uagents_core``. Full write-up for maintainers:
``simple_a2a_agent/docs/uagents-av-chat-task-response.md``.

See: ``uagents_core.adapters.a2a.agentverse_sdk.AgentverseA2AStarletteApplication._chat``
(~lines 326–337 in the installed package).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from uuid import uuid4

from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def _join_text_parts(parts: list) -> str:
    return ''.join(
        p.get('text', '')
        for p in parts
        if isinstance(p, dict) and p.get('kind') == 'text'
    )


def _text_from_a2a_json_result(answer: dict | None) -> str:
    if not answer or not isinstance(answer, dict):
        return ''
    top = answer.get('parts')
    if isinstance(top, list):
        return _join_text_parts(top)
    if answer.get('kind') == 'task':
        status = answer.get('status')
        if isinstance(status, dict):
            msg = status.get('message')
            if isinstance(msg, dict):
                smp = msg.get('parts')
                if isinstance(smp, list):
                    t = _join_text_parts(smp)
                    if t:
                        return t
        chunks: list[str] = []
        for art in answer.get('artifacts') or []:
            if not isinstance(art, dict):
                continue
            for p in art.get('parts') or []:
                if isinstance(p, dict) and p.get('kind') == 'text':
                    chunks.append(p.get('text', ''))
        return ''.join(chunks)
    return ''


def apply_agentverse_a2a_chat_patch() -> None:
    """Replace AgentverseA2AStarletteApplication._chat with task-aware parsing."""
    import uagents_core.adapters.a2a.agentverse_sdk as av

    from uagents_core.contrib.protocols.chat import (
        ChatAcknowledgement,
        ChatMessage,
        StartSessionContent,
        TextContent,
    )
    from uagents_core.identity import Identity
    from uagents_core.utils.messages import send_message_to_agent

    async def _chat_patched(self, request: Request) -> JSONResponse:
        env, msg = await av._parse_chat_request(request, av._agent.verify_envelope)

        if isinstance(msg, ChatAcknowledgement):
            return JSONResponse({})

        send_message_to_agent(
            destination=env.sender,
            msg=ChatAcknowledgement(
                timestamp=datetime.now(), acknowledged_msg_id=msg.msg_id
            ),
            sender=Identity.from_seed(av._agent.uri.key, 0),
            agentverse_config=av._agent.uri.agentverse_config,
        )

        if len(msg.content) == 1 and isinstance(msg.content[0], StartSessionContent):
            return JSONResponse({})

        text = ''
        for item in msg.content:
            if isinstance(item, TextContent):
                text += item.text

        a2a_msg = {
            'jsonrpc': '2.0',
            'id': str(msg.msg_id),
            'method': 'message/send',
            'params': {
                'message': {
                    'kind': 'message',
                    'role': 'user',
                    'messageId': str(uuid4()),
                    'parts': [{'kind': 'text', 'text': text}],
                }
            },
        }

        async def a2a_receive():
            return {
                'type': 'http.request',
                'body': json.dumps(a2a_msg).encode(),
                'more_body': False,
            }

        a2a_request = Request(
            scope=request.scope, receive=a2a_receive, send=request._send
        )
        response = ''

        try:
            resp = await super(av.AgentverseA2AStarletteApplication, self)._handle_requests(
                a2a_request
            )
            a2a_response = json.loads(resp.body)
            answer = a2a_response.get('result')
            response = _text_from_a2a_json_result(answer)
            if not response and answer is not None:
                logger.debug('Agentverse patch: empty text from result keys=%s', list(answer))

        except (json.JSONDecodeError, TypeError) as e:
            logger.warning('Failed to parse A2A agent JSON: %s', e)
            response = 'Sorry, malformed response from the agent, please retry later.'
        except Exception as e:
            logger.warning('Failed to process request by a2a agent: %s', e)
            response = 'Sorry, agent is not reachable'

        av_response = ChatMessage(
            timestamp=datetime.now(timezone.utc),
            msg_id=uuid4(),
            content=[TextContent(type='text', text=response)],
        )
        send_message_to_agent(
            destination=env.sender,
            msg=av_response,
            sender=Identity.from_seed(av._agent.uri.key, 0),
            agentverse_config=av._agent.uri.agentverse_config,
            session_id=env.session,
        )

        return JSONResponse({})

    av.AgentverseA2AStarletteApplication._chat = _chat_patched
    logger.info('Applied Agentverse A2A task/artifact response patch for /av/chat')
