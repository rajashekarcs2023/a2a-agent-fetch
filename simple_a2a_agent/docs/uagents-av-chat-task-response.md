# Agentverse `/av/chat` and A2A `message/send` task responses

This note is for **Fetch / `uagents-core` maintainers** and anyone integrating **Agentverse** with **`a2a-sdk`** Python servers.

**Package vs extra:** the code lives in the **`uagents-core`** package under **`uagents_core/adapters/a2a/`**. Apps install **`uagents-core[a2a]`** to pull A2A-related dependencies and use this adapter — it is not a separate library, only an **optional extra** on the same repo.

### Upstream fix vs this repo (what to actually run)

| Where | What to do |
|-------|------------|
| **`uagents-core` (Fetch), A2A adapter** | Merge **task-aware** parsing into **`AgentverseA2AStarletteApplication._chat`** in **`agentverse_sdk.py`** — that is the **universal** fix this document proposes. |
| **This project’s runtime** | **Keep** the Fetch / innovation-lab pattern: final user-visible text via **`a2a.utils.new_agent_text_message`** on the **`EventQueue`** in **`simple_a2a_agent/simple_a2a_agent/executor.py`** (**`A2A_FINAL_TEXT_MESSAGE`** default **on**). That is the **supported** workaround for Agentverse **today** and does **not** require forking or monkey-patching installed packages. |
| **Reference patch (optional)** | **`simple_a2a_agent/simple_a2a_agent/agentverse_task_result_patch.py`** — mirrors what upstream should do inside **`_chat`**. For local experiments or diffs only; **not** the default production strategy. |
| **Upstream module snapshot (this repo)** | **`uagents-core-a2a/agentverse_sdk.py`** — vendored **`agentverse_sdk`** for review. Line numbers in this note match this file and **`uagents-core[a2a]`** as in the footer; they **shift** in other releases. |

## Symptom

When Agentverse (or any client using the **`AgentverseA2AStarletteApplication`** path) forwards chat to the agent via internal **`message/send`**, users may see:

```text
Failed to parse A2A agent response: 'parts'
Sorry, malformed response from the agent, please retry later.
```

The agent process still completes work (LLM + tools); the failure happens in **`AgentverseA2AStarletteApplication._chat`** after **`message/send`** returns: the handler assumes every **`result`** has top-level **`parts`**.

## Root cause

**HTTP path:** **`build()`** registers **`POST /av/chat`** (`DEFAULT_AGENTVERSE_CHAT_ENDPOINT`) onto **`_chat`** — see vendored **`uagents-core-a2a/agentverse_sdk.py`**.

Inside **`_chat`**, the library builds a synthetic JSON-RPC **`message/send`**, **`await`s `super()._handle_requests(a2a_request)`**, then **`json.loads(resp.body)`** and reads **`result`**. For this snapshot (**`uagents-core[a2a]==0.4.5a2`**, **`uagents-core-a2a/agentverse_sdk.py`** lines **326–338**; numbers shift upstream) it does:

```python
a2a_response = json.loads(resp.body)
answer = a2a_response.get("result")
if answer is not None:
    for part in answer["parts"]:  # KeyError when `answer` is a Task (no "parts" key)
        if part["kind"] != "text":
            continue
        response += part["text"]
```

That **`KeyError`** is caught together with **`json.JSONDecodeError`** (`except (json.JSONDecodeError, KeyError)`), which produces the log **`Failed to parse A2A agent response: 'parts'`** and the user-visible **“malformed response”** string — so the bug is a **shape assumption**, not a failed JSON parse.

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

## Universal fix (belongs in the `uagents-core` A2A adapter)

**Location:** `uagents_core/adapters/a2a/agentverse_sdk.py` → **`AgentverseA2AStarletteApplication._chat`**, in the block that parses **`resp.body`** after `super()._handle_requests(a2a_request)` (in a venv: `site-packages/uagents_core/.../agentverse_sdk.py`). Prefer a PR against the **`uagents-core`** repo and a release consumers pick up via **`uagents-core[a2a]`**, not long-term edits under `site-packages`.

**Change:** Do not assume **`answer["parts"]`**. Extract text in an order such as:

1. If **`answer`** has **`parts`** (list) → treat as message-shaped; concatenate **`kind == "text"`** parts (existing behavior).
2. Else if **`answer`** is a **dict** with **`kind == "task"`** (or equivalent task shape from JSON):
   - Prefer text from **`status.message.parts`** if present.
   - Else concatenate text from **`artifacts`** → each artifact’s **`parts`** where **`kind == "text"`**.

**Reference logic** (same idea as **`_text_from_a2a_json_result`** in this repo’s  
`simple_a2a_agent/simple_a2a_agent/agentverse_task_result_patch.py`):

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

Use the extracted string as **`response`** before building **`ChatMessage`** / **`TextContent`** for Agentverse. (If you deserialize to **`a2a.types`** models first, apply the same cases: message **`parts`** vs task **`status.message`** / **`artifacts`**.)

### Testing (upstream)

1. Run an A2A server that completes with **`DefaultRequestHandler`** + task/artifact flow (no `new_agent_text_message` workaround).
2. After **`agentverse_sdk.init(...)`**, **`POST /av/chat`** with a valid chat envelope.
3. Expect no **`'parts'`** KeyError and chat UI shows text from task artifacts.

## Workaround for agent authors (not universal)

Until **`_chat`** is fixed, any agent can use **`a2a.utils.new_agent_text_message`** on the **`EventQueue`** so the JSON-RPC shape matches what the current parser expects (see the table at the top for how **this** repo enables that by default).

---

*Observed with: `uagents-core[a2a]==0.4.5a2`, `a2a-sdk` (protocol 0.3.x), Python 3.12.*
