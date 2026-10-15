# Agent Chat model switching and evaluation

Agent Chat has an OCI Responses model selector and optional File Search toggle.
Changing either starts a fresh conversation. Existing business workflows, email
drafts and other model consumers retain `ENTERPRISE_AI_MODEL_ID`. No additional
LangChain wrapper is required: the existing Responses adapter and bounded
LangGraph function-tool loop are reused.

The evaluation section below chat compares two or three profiles using the same
prompt, fresh context and the same read-only tool permissions. Calls run
sequentially, with independent live database/policy reads. This is an interactive
comparison, not a statistically controlled benchmark. Hosted conversation history
and live approval context are not reused by evaluations.

Evaluation starts collapsed to keep the chat focused. Select **Evaluation &
metrics** in the chat toolbar or **Explore** in the panel header to open it.
Collapsing preserves inputs, results and running comparisons. The header shows
comparison progress while collapsed. The panel supports keyboard controls,
mobile layouts and reduced-motion preferences; normal transitions take 120–180 ms.

## Configure models

### Verified local configuration (13 October 2026)

The repository `.env` now configures Gemini 2.5 Flash
(`google.gemini-2.5-flash`) and GPT-OSS 120B (`openai.gpt-oss-120b`). Both
completed live OCI Responses function-tool loops, saved conversations and history
follow-ups in the configured Hyderabad project. GPT-OSS continuation requests
normalize output items to supported input shapes; Gemini thought signatures are
preserved. Availability remains specific to this project and region.

With subsequent user approval, optional File Search is enabled for both profiles.
The `retail_supplier_policies` vector store in `AIML_Internal_Initiatives`,
Hyderabad, contains all four completed supplier PDFs (SUP001–SUP004). Its ID is
`vs_hyd_qtdqk64xgcql0jflogzw6g230n4rocvumlxrubkhu7h3znlt`. Semantic search and
native Responses File Search succeeded with both configured models. The app's
chat and comparison toggles remain opt-in; the global File Search default is
unchanged. Source filenames appeared in answer text, but OCI returned no
structured file-citation annotations in these checks.

The interface does not display cost estimates or rate cards. No official billing
feed is connected. Existing backend price fields are retained for compatibility;
they are not used by the dashboard, including for previously recorded runs.

The current local deployment uses local agent mapping. Registry routing has not
been demonstrated against live remote agents in this configuration.

Only administrator-configured profiles are exposed. The application cannot infer
regional model access from a model name. Verify each exact model ID or supported
model endpoint in your OCI region before adding it. Selecting an unavailable model
reports a failed run; there is no silent provider/model fallback.

Keep the existing OCI credentials, Responses endpoint, project ID and database
configuration. Set `ENTERPRISE_AI_RESPONSES_ENABLED=true` and
`ENTERPRISE_AI_API_MODE=responses`. `ENTERPRISE_AI_PROJECT_ID` is required for the
OCI IAM path used by the local configuration.

Add this structure to your existing private JSON configuration (do not replace
the rest of that file). Replace the second model ID with a model actually
available through your OCI Responses endpoint:

```json
{
  "chat_model_profiles": [
    {
      "id": "baseline",
      "label": "Gemini 2.5 Flash",
      "model_id": "google.gemini-2.5-flash",
      "file_search": false
    },
    {
      "id": "comparison",
      "label": "OCI comparison model",
      "model_id": "REPLACE_WITH_AVAILABLE_OCI_RESPONSES_MODEL_ID",
      "file_search": false
    }
  ]
}
```

Alternatively, set `CHAT_MODEL_PROFILES` to a JSON array in the environment/.env.
The first profile is the chat default. With no profiles, the existing baseline is
the only option. A second distinct profile is required for comparison.
Settings precedence remains explicit constructor values, private JSON,
environment, then .env. Restart after configuration changes.

In a distributed deployment, configure profiles on the **retail agent**; the
backend gets its catalog through the existing signed agent transport. Backend and
retail-agent source changes must be deployed together. Do not overwrite the
separately deployed Langfuse backend with this repository snapshot; merge this
feature into its source first, as noted in the main README.

## Retrieval and tools

Configure `ENTERPRISE_AI_VECTOR_STORE_IDS` with already ingested policy vector
stores, and set `file_search: true` only for profiles whose Responses/tool support
has been verified. The toggle attaches native `type: file_search` to Responses.
Returned File Search statuses and file citations appear under Execution status &
evidence. Uploading/ingesting documents is a separate existing setup step.

Function tools continue using the server's tool schema registry and allowlist.
Responses chooses functions; LangGraph validates arguments and executes them
through local functions or the existing remote MCP transport. These are
application function tools, not native Responses remote-MCP tools. No mutation or
approval tool is exposed by this chat loop.

For distributed agents, active registry mappings resolve the retail endpoint;
configured endpoints remain the existing fallback. Events report `registry`,
`configured_endpoint`, or local execution as actually used. A model switch does
not create agents or registry records.

Constrained NL2SQL remains controlled by `ENTERPRISE_AI_NL2SQL_ENABLED` and uses the
existing validated query plans and parameterized SQL. A different chat model may
choose different tools/arguments or produce different explanations; unchanged
business rules do not guarantee identical model behavior. Invalid calls fail
closed and appear in the evaluation.

## Measurement and quality

- Usage is summed across all Responses calls in a turn. Missing usage is unknown,
  never zero. Cached/reasoning token details are shown when the provider reports
  them; reasoning tokens are already included in output tokens.
- Latency records model-call time and full server turn time (including tools and
  history operations); browser/network rendering time is excluded.
- Session metrics show reported tokens, recorded tool calls, latency and failed
  runs. Cost is omitted because official billing data is not integrated.
- Quality displays tool success, citation presence, optional expected-term checks
  and a 1–5 reviewer rating. These are not automatic truth/groundedness judgments.
  Review the actual answer against the retrieved evidence.
- Execution status is visible after completion. Private chain-of-thought is not
  exposed. Application events are sanitized session-specific operational records,
  not access to arbitrary server logs.
- Each answer has a persistent execution map based only on recorded model, tool
  and run-outcome events. Routing and retrieval configuration appear separately.
  **Step through** manually highlights recorded steps without replaying requests,
  delaying execution or simulating live progress. Technical details retain sources
  and raw event evidence.
- A comparison table shows outcomes, latency, tokens, tool calls and reviewer
  ratings. The multi-constraint prompt starter requests grounded inventory and
  supplier analysis, trade-offs and missing information. It does not guarantee
  that any particular model will succeed or outperform another.

## Replacing models and regional access

Current profiles share the configured Responses endpoint, project and identity.
A compatible replacement available there can be registered as another model
profile without changing the agent graph or business tools. Validate function
calling, continuation and query quality before adopting it. Changing selection
starts a fresh chat; an already running comparison keeps its captured selection.

The supplied Chicago SDK example calls `GenerativeAiInferenceClient.chat()`;
that is not a Responses compatibility test. Before enabling that model, obtain
its Responses-compatible ID, a Chicago Generative AI project, and authorized
access. Model OCIDs for the Chat API are not sufficient evidence of Responses
support. Cross-region profiles would require a scoped transport/configuration
extension; this UI update does not switch endpoints or credentials. The existing
Hyderabad endpoint is preserved. Optional RAG uses the verified Hyderabad store
described above.

Source: [OCI Responses API prerequisites and endpoint](https://docs.oracle.com/en-us/iaas/Content/generative-ai/responses-api.htm).

## Persistence and limits

`CHAT_METRICS_PATH` defaults to `runtime/chat-metrics.sqlite3`. Mount a private,
writable persistent directory in containers. The store includes generated answers
and comparison prompts; restrict host file access just as for chat history. It
retains the latest 100 records per session, removing records older than seven days
when new records are saved. Different sessions cannot read or rate each other's
runs. Logout/new session does not expose previous session records.

The comparison queue supports one job per session, at most two jobs per process,
and two or three model runs per job. Each graph has the existing four-model-call
and eight-function-call limits. Jobs are not automatically replayed after a server
restart. Completed results persist, and interrupted jobs are marked when queried.
Use one backend process/replica for this POC queue; multiple replicas need a shared
job queue and shared telemetry storage before deployment. Polling stops when the
page is inactive. A failed model does not prevent the next model from running.

## Demo sequence

1. In Agent Chat, choose a model and ask which item IDs have stockout risk.
2. Review the execution map and select Step through to explain individual calls.
3. Choose another configured model; submit the same question in the new chat.
4. When a completed store and a compatible profile are verified, enable File
   Search and ask a policy question covered by an ingested document.
   Inspect actual citations and the native File Search event.
5. Under Model evaluation, select two profiles, enter a common prompt and optional
   expected terms, then run the comparison. Review answers and rate quality.
6. Inspect the performance comparison table and filter Application events by tool
   or run ID. Use reviewer ratings to assess answer quality against actual data.
7. In a distributed deployment with an active registry mapping, verify that the
   routing event says `registry`. Local mode should correctly say `local`.

## Local commands

From `C:\Users\mrmishr\Desktop\Retail Project\retail-inventory-agent`:

```powershell
$env:RETAIL_ENV_FILE = 'C:\Users\mrmishr\Desktop\Retail Project\retail-inventory-agent\.env'
.\.venv\Scripts\python.exe -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000
```

In a second terminal:

```powershell
Set-Location 'C:\Users\mrmishr\Desktop\Retail Project\retail-inventory-agent\frontend'
$env:API_PROXY_TARGET = 'http://127.0.0.1:8000'
node.exe node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5173 --strictPort
```

Open `http://127.0.0.1:5173`. These commands use already installed dependencies.
Alternatively, `powershell.exe -NoProfile -ExecutionPolicy Bypass -File
.\scripts\start-chat-local.ps1` starts the backend using this repository's `.env`
and `.venv`. It preserves notification and routing settings from the configuration.
For a clean dependency installation, use the repository's backend dependency
instructions and `npm.cmd ci` in frontend.

Validation:

```powershell
$env:PYTHONPATH = 'backend'
$env:RETAIL_ENV_FILE = 'test-environment-not-present.env' # keep offline tests separate from private live config
.\.venv\Scripts\python.exe -m pytest tests/test_chat_evaluation.py tests/test_deployment_v2_release.py -q
Set-Location frontend
node.exe node_modules\vite\bin\vite.js build
node.exe node_modules\@playwright\test\cli.js test --config playwright.evaluation.config.js
```

Browser tests use labeled API fixtures, including illustrative token/cost data;
they verify UI behavior and are not evidence of live OCI model availability or
performance. The Python tests exercise the actual graph/Responses parser with
mocked provider and tool boundaries.
