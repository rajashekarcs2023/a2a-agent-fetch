# Agentverse `/av/chat` and A2A `message/send` task responses

This note is for **Fetch / `uagents-core` maintainers** and anyone integrating **Agentverse** with **`a2a-sdk`** Python servers.

### Upstream fix vs this repo (what to actually run)

| Where | What to do |
|-------|------------|
| **`uagents-core` (Fetch)** | Merge **task-aware** parsing into **`AgentverseA2AStarletteApplication._chat`** — that is the **universal** fix this document proposes. |
| **This project’s runtime** | **Keep** the Fetch / innovation-lab pattern: final user-visible text via **`a2a.utils.new_agent_text_message`** on the **`EventQueue`** (`executor.py`, **`A2A_FINAL_TEXT_MESSAGE`** default **on**). That is the **supported** workaround for Agentverse **today** and does **not** require forking or monkey-patching installed packages. |
| **`agentverse_task_result_patch.py`** | **Reference only** — mirrors what upstream should do inside `_chat`. Use **only** for local experiments or diffing against a PR; **do not** treat it as the default production strategy. |

**Bottom line:** Suggest the **adapter change** to Fetch; in **our** code, **follow Fetch’s executor pattern**, not the monkey patch.

## Symptom

When Agentverse (or any client using the **`AgentverseA2AStarletteApplication`** path) forwards chat to the agent via internal **`message/send`**, users may see:

```text
Failed to parse A2A agent response: 'parts'
Sorry, malformed response from the agent, please retry later.
```

The agent process still completes work (LLM + tools); the failure happens when **`uagents_core`** parses the JSON-RPC **response body**.

## Root cause

In **`uagents_core.adapters.a2a.agentverse_sdk`**, method **`AgentverseA2AStarletteApplication._chat`** (approx. lines 326–337 in `uagents-core==0.4.5a2`) does effectively:

```python
a2a_response = json.loads(resp.body)
answer = a2a_response.get("result")
for part in answer["parts"]:  # KeyError when `answer` is a Task
    ...
```

For many **`a2a-sdk`** + **`DefaultRequestHandler`** flows, a successful non-streaming **`message/send`** returns a **`result` that is a `Task`**, with final text under **`artifacts[].parts[]`**, not **`result["parts"]`** at the top level.

So **`answer["parts"]`** is invalid for conforming servers that complete with a **task artifact** completion path.

**Example** — `message/send` success where **`result`** is task-shaped (no top-level **`parts`**); text lives under **`artifacts`** (and optionally **`status.message`**):

```json
{
  "jsonrpc": "2.0",
  "id": "1",
  "result": {
    "kind": "task",
    "id": "019a…",
    "status": { "state": "completed" },
    "artifacts": [
      {
        "artifactId": "…",
        "parts": [{ "kind": "text", "text": "Final answer text here." }]
      }
    ]
  }
}
```

This is the same conceptual gap that Fetch’s own **innovation-lab-examples** work around when **calling remote agents** — see `_extract_text_from_response` in  
[`orchestrator_agent.py`](https://github.com/fetchai/innovation-lab-examples/blob/main/launch-your-a2a-research-team/orchestrator_agent.py) (handles **`Message`**, **`Task.status.message`**, **`Task.artifacts`**).

## Universal fix (belongs in `uagents_core`)

**Location:** `uagents_core/adapters/a2a/agentverse_sdk.py` → **`AgentverseA2AStarletteApplication._chat`**, in the block that parses **`resp.body`** after `super()._handle_requests(a2a_request)`.

**Change:** Do not assume **`answer["parts"]`**. Extract text in an order such as:

1. If **`answer`** has **`parts`** (list) → treat as message-shaped; concatenate **`kind == "text"`** parts (existing behavior).
2. Else if **`answer`** is a **dict** with **`kind == "task"`** (or equivalent task shape from JSON):
   - Prefer text from **`status.message.parts`** if present.
   - Else concatenate text from **`artifacts`** → each artifact’s **`parts`** where **`kind == "text"`**.

**Reference logic** (tested as a local monkey-patch; see repository file  
`simple_a2a_agent/simple_a2a_agent/agentverse_task_result_patch.py`, function **`_text_from_a2a_json_result`**):

```python
def _join_text_parts(parts: list) -> str:
    return "".join(
        p.get("text", "")
        for p in parts
        if isinstance(p, dict) and p.get("kind") == "text"
    )


def _text_from_a2a_json_result(answer: dict | None) -> str:
    if not answer or not isinstance(answer, dict):
        return ""
    top = answer.get("parts")
    if isinstance(top, list):
        return _join_text_parts(top)
    if answer.get("kind") == "task":
        status = answer.get("status")
        if isinstance(status, dict):
            msg = status.get("message")
            if isinstance(msg, dict):
                smp = msg.get("parts")
                if isinstance(smp, list):
                    t = _join_text_parts(smp)
                    if t:
                        return t
        chunks: list[str] = []
        for art in answer.get("artifacts") or []:
            if not isinstance(art, dict):
                continue
            for p in art.get("parts") or []:
                if isinstance(p, dict) and p.get("kind") == "text":
                    chunks.append(p.get("text", ""))
        return "".join(chunks)
    return ""
```

Use the extracted string as **`response`** before building **`ChatMessage`** / **`TextContent`** for Agentverse.

You may deserialize with **`a2a.types`** models instead of raw dicts; the **cases** (message `parts` vs task `status.message` / `artifacts`) stay the same.

### Where to edit (installed package)

Typical path: `<venv>/lib/python3.*/site-packages/uagents_core/adapters/a2a/agentverse_sdk.py` — class **`AgentverseA2AStarletteApplication`**, method **`_chat`**. Prefer fixing **`uagents-core`** in source, releasing, and changelog entry.

### Testing (upstream)

1. Run an A2A server that completes with **`DefaultRequestHandler`** + task/artifact flow (no `new_agent_text_message` workaround).
2. After **`agentverse_sdk.init(...)`**, **`POST /av/chat`** with a valid chat envelope.
3. Expect no **`'parts'`** KeyError and chat UI shows text from task artifacts.

### Maintainer blurb (copy-paste)

> **`AgentverseA2AStarletteApplication._chat`** should parse **`message/send`** **`result`** when it is a **Task** (artifacts), not only top-level **`parts`**. Symptom: **`KeyError: 'parts'`** / “malformed response”. Align extraction with **`_extract_text_from_response`** in Fetch **`innovation-lab-examples`** `orchestrator_agent.py`.

## Workaround for agent authors (not universal)

Until the adapter is fixed upstream, agents can **emit a final `Message`** with **`a2a.utils.new_agent_text_message`** on the **`EventQueue`** — same idea as Fetch’s examples when consuming A2A responses. That can change the observable RPC shape so the **current** parser succeeds. It is **per-agent**; once **`_chat`** parses **task-shaped** `result` correctly, this workaround becomes optional (not required for Agentverse chat).

## Files in this repo

| File | Role |
|------|------|
| `simple_a2a_agent/simple_a2a_agent/agentverse_task_result_patch.py` | **Reference** implementation of the adapter-side fix (not imported by default). |
| `simple_a2a_agent/simple_a2a_agent/executor.py` | Uses **`new_agent_text_message`** for the final reply by default (`A2A_FINAL_TEXT_MESSAGE`), compatible with Agentverse today. |

---

*Observed with: `uagents-core[a2a]==0.4.5a2`, `a2a-sdk` (protocol 0.3.x), Python 3.12.*
