#!/usr/bin/env python3
"""Test your A2A server with the official Python client (no second agent required).

A2A is HTTP + JSON-RPC-style messages. Anything that can:
  1) GET the agent card (usually `/.well-known/agent-card.json`), and
  2) POST A2A `sendMessage` requests
can talk to your server—including this script, curl, or *optionally* another agent
that delegates work (like `airbnb_planner_multiagent`'s host).

Usage (with the server running, venv active, package installed):
  python examples/test_a2a_client.py --url http://127.0.0.1:10000 \\
      --message "What is the weather in Austin, TX?"

Or with env:
  export A2A_AGENT_URL=https://your-subdomain.ngrok-free.app
  python examples/test_a2a_client.py --message "Hello"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import uuid

import httpx
from a2a.client import A2ACardResolver, A2AClient
from a2a.types import MessageSendParams, SendMessageRequest
from dotenv import load_dotenv, find_dotenv


async def run(base_url: str, text: str) -> None:
    base_url = base_url.rstrip('/')
    timeout = httpx.Timeout(300.0, connect=30.0)
    async with httpx.AsyncClient(timeout=timeout) as http:
        resolver = A2ACardResolver(http, base_url)
        card = await resolver.get_agent_card()
        print(f"Card: name={card.name!r} url={card.url!r}\n")

        client = A2AClient(http, card, url=base_url)
        message_id = uuid.uuid4().hex
        payload = {
            'message': {
                'role': 'user',
                'parts': [{'type': 'text', 'text': text}],
                'messageId': message_id,
            },
        }
        req = SendMessageRequest(
            id=message_id,
            params=MessageSendParams.model_validate(payload),
        )
        # a2a-sdk expects a positional request (keyword message_request= was removed in newer versions).
        resp = await client.send_message(req)
        print(resp.model_dump_json(indent=2, exclude_none=True))


def main() -> None:
    load_dotenv(find_dotenv(usecwd=True))
    p = argparse.ArgumentParser(description='Send one message to an A2A agent.')
    p.add_argument(
        '--url',
        default=os.getenv('A2A_AGENT_URL', 'http://127.0.0.1:10000'),
        help='Base URL of the A2A server (default: env A2A_AGENT_URL or localhost:10000)',
    )
    p.add_argument(
        '--message',
        '-m',
        default=os.getenv(
            'TEST_MESSAGE', 'What is the weather in Los Angeles, CA?'
        ),
        help='User message to send',
    )
    args = p.parse_args()
    asyncio.run(run(args.url, args.message))


if __name__ == '__main__':
    main()
