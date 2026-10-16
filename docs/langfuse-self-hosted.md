# Self-hosted Langfuse

The application can export workflow, OCI generation, external-provider generation,
remote-agent and MCP-tool observations to an existing self-hosted Langfuse instance.
The public deployment branch reads the shared project settings from private
runtime JSON. The default without those settings is disabled. Langfuse is already
hosted separately and is not provisioned by the retail app deployment.

## Shared dashboard setup

See [Dashboard shared Langfuse setup](dashboard-shared-langfuse.md). The dashboard
VM operator installs a small hook and configures its private settings file once.
Each new deployment saves the shared settings in private run state and forwards
them to backend, retail-agent, supplier-agent and mcp-server. The frontend never
receives project keys. Existing runs do not inherit later changes.

No per-user Langfuse key entry is needed after that VM setup. Each user's OCI
account and application resources remain independent. Deployed services need
network access to the shared Langfuse ingestion endpoint, even if it resides in
a different tenancy. A laptop or dashboard SSH tunnel is not enough by itself.

## Local use and manual overrides

Set LANGFUSE_TRACING_ENABLED=true, LANGFUSE_BASE_URL, LANGFUSE_PUBLIC_KEY and
LANGFUSE_SECRET_KEY in a private environment file. Install the optional SDK with
`python -m pip install -e "./backend[tracing]"` and restart the backend. Source
images already include the SDK. Explicit false disables tracing.

For an existing service config, `scripts/configure_langfuse.py` can add all three
project fields to a new private JSON without replacing its other settings.
Do not manually change configuration objects belonging to an in-progress dashboard
run: recovery checks their saved contents. Prefer a new deployment using the hook.

The optional diagnostic is:

```powershell
.\.venv\Scripts\python.exe scripts/verify_langfuse.py --langfuse-env C:\private\langfuse.env --output C:\private\langfuse-check.json
```

It sends one synthetic observation without model or database calls and requires
its trace ID to be read back. In the deployed app, open Evaluation & metrics >
Cloud monitoring > Langfuse tracing > Open Langfuse. Sign in to the shared project,
send a chat question, and confirm the trace. Configuration status alone does not
confirm ingestion.

## Scope and checks

Exported inputs and outputs are bounded and sanitized for known secret fields and
email patterns; business text can still be included. Only run approved demo data.
SDK/export failures do not interrupt business operations. Incomplete configuration
with tracing explicitly enabled is rejected at startup. Token counts are exported
when returned by the provider; cost availability depends on Langfuse model pricing.

Nested spans work within one service. Remote services currently record separate
traces/correlation metadata; this change does not introduce distributed trace-parent
propagation or promise a single joined waterfall. File Search is an OCI built-in,
not an MCP call. Private model reasoning is not exported by these generation hooks.

## Chat request limits

There is no fixed lifetime token quota per application chat. Default OCI output is
6000 tokens per generation (configurable from 1 to 16000); external connections
request 4096 by default (256 to 16000). Providers may impose stricter limits.
One read-only chat request is limited to four model calls, eight tool calls, and
160000 serialized context characters. User messages are limited to 12000 characters, and chat answers to 16000 characters.
Application history loads up to 20 recent message records, keeping whole exchanges
within a 60000-character history budget. This does not remove saved messages. OCI hosted conversation
state can retain older context and is also subject to the provider context window.
The browser request timeout is 90 seconds. Starting a new chat reduces conversation
context but does not reset provider billing or rate limits.

## Session chats

The sidebar lists chats from the current signed-in browser session. New chat starts
fresh model/workflow context; previous conversations and recorded workflow steps
remain available. Selecting an earlier chat restores its conversation ID, model
selection and transcript. Saved request controls reload authoritative server state;
historical approval snapshots never authorize another action.

Messages and completed activity are retained in memory and sessionStorage, including
after a page refresh. There is no 60-message truncation. Signing out or session
expiry clears this transcript cache. It is not cross-device chat history. Provider
API keys are not included in chat snapshots. If browser storage is full, a notice
explains that further changes survive only while the page remains open.

Model switching keeps the transcript but starts fresh model context. Provider
billing, rate limits and context limits still apply. OpenAI and Anthropic require
a valid provider API key and an accessible function-calling model; catalog presence
does not guarantee available credit or quota. OCI File Search is unavailable for
external models; the application's permitted function tools remain available.
