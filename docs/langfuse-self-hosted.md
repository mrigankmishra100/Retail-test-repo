# Self-hosted Langfuse

The application can export workflow, OCI generation, external-provider generation,
remote-agent and MCP-tool observations to an existing self-hosted Langfuse instance.
Tracing defaults to disabled until a URL and project keys are configured. Installing
this app does not install a Langfuse server. This change does not start a VM service.

## Activate after hosting

1. Start your separately managed Langfuse instance, create a project and generate
   its project public and secret keys. Use the instance URL, not the retail app URL.
2. Store the following in a private `.env` file outside Git and image build contexts:

   ```dotenv
   LANGFUSE_TRACING_ENABLED=true
   LANGFUSE_BASE_URL=https://your-langfuse-host
   LANGFUSE_PUBLIC_KEY=your-project-public-key
   LANGFUSE_SECRET_KEY=your-project-secret-key
   LANGFUSE_ENVIRONMENT=poc
   ```

3. Local installation: `python -m pip install -e "./backend[tracing]"`, then restart
   the backend with that private environment file. Public source Docker builds
   already install the pinned SDK from `backend/requirements.lock`.
4. Hosted services: supply these fields to backend, retail-agent, supplier-agent
   and mcp-server private runtime JSON. Do not supply keys to the frontend.
   The app's `prepare_runtime_configs.py` forwards enabled tracing settings.
   The separate deployment dashboard does not automatically collect these values.
   Preserve its generated service configuration and use this additive helper:

   ```powershell
   .\.venv\Scripts\python.exe scripts/configure_langfuse.py --service retail-agent --config C:\private\retail-agent.json --langfuse-env C:\private\langfuse.env --output C:\private\retail-agent-with-tracing.json
   ```

   Repeat for the other three server services, upload the new private configurations
   through the existing deployment configuration flow and restart the services.
   The helper refuses to overwrite existing output files and retains the original
   deployment endpoints, service keys and database settings. Future dashboard
   regenerations must retain these fields or tracing will return to disabled.
5. Open Evaluation & metrics > Cloud monitoring > Langfuse tracing. The status
   describes this backend's configuration only; it does not probe the instance or
   confirm agent configuration. Open Langfuse, sign in, choose the project and
   inspect Traces after making a new chat request. Allow for asynchronous ingestion.

Hosted OCI services need network access to the Langfuse ingestion endpoint.
An SSH tunnel from your laptop provides browser access only. Use a reachable HTTPS
endpoint for deployed services. Do not use their own localhost as the VM address.

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
100000 serialized context characters. User messages are limited to 4000 characters, and chat answers to 16000 characters.
Application history loads eight recent message records; OCI hosted conversation
state can retain older context and is also subject to the provider context window.
The browser request timeout is 90 seconds. Starting a new chat reduces conversation
context but does not reset provider billing or rate limits.
