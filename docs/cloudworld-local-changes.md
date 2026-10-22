# Cloudworld supplier routing and shared OpenAI

These changes are in the Retail source only. They add no database tables or migrations,
change no approval actions, and retain the existing five-service image contract.

## Supplier behavior

- Without a usable delivery connection, delivery questions receive a short explanation
  of supported supplier scope, without API/token/registration instructions or carrier facts.
- With a usable connection, explicit delivery, distributor, courier and known-carrier
  questions call `ask_delivery_agent` after the existing prompt guardrails. The answer
  is displayed directly. A model cannot skip this route and claim the connection is absent.
- Setup questions and contextual follow-ups still use the supplier model and its scoped tools.
- Scope notices and lookup failures remain visible in the transcript but are not saved
  as model facts. Each turn rechecks the connection, active registry entry and session.
- Registering a Custom agent in the supplier workspace also selects it for delivery.
  The optional bearer field is always visible; blank sends no Authorization header.
  The latest selection replaces the previous destination for that supplier login session.
  Inactive entries must still be activated. Existing entries can be reconnected from their row.
- The API must implement the existing question/answer contract. Registration does not
  infer a protocol from a name or perform a live authorization check.

The destination still requires public HTTPS with a trusted certificate. Dotted OCI
application IDs are accepted in the API path. An OCI URL must reach the actual API
and accept the existing bearer or unauthenticated request; this REST adapter does
not sign outbound OCI IAM requests.

## Shared OpenAI

**OpenAI…** appears after the two baseline retail models. It loads the catalog using
the key configured on the Retail model host. Users select a model without receiving
or entering that key. The existing External model option remains available.

The selection applies to retail chat and remaining workflow AI steps through the
existing provider adapter. Supplier chat retains its configured supplier model.
Approvals, permissions and deterministic tools keep their existing behavior.
Choose a text model supporting the Responses API and function calling; the model
catalog also contains specialized models that cannot run this workflow. Catalog
membership does not guarantee quota or compatibility. OCI File Search remains
unavailable for external models, while policy function tools remain available.

The model picker is a compact centered dialog. OpenAI selection no longer sends
the former 4096 `max_output_tokens` value: it uses the provider/model default.
That parameter limits one generation (including reasoning), not the total length
of a chat or workflow. Model context/output limits and the app's existing request
size, history, tool-loop and timeout safeguards still apply. Other provider defaults
are unchanged. There is no unlimited model/context setting.

The POC environment badge has been removed. Cost panels appear only for runs with
complete reported usage and a saved model-cost estimate; unpriced models do not
show empty estimate cards. Coverage remains explicit for partially priced sessions.
These are model-token estimates, not cloud bills. Missing usage displays as
"Not reported" instead of an apparent zero.

Set `OPENAI_API_KEY` in the local backend environment or hosted **retail-agent** private
runtime JSON. It is optional: omitting it leaves baseline models operational and
shows a configuration error only when OpenAI is selected. No key belongs in source,
Docker build arguments, frontend configuration or Git. Shared selections use the
existing session model store and must be reselected after a server restart.

Prepare a new private hosted config from the existing Retail-agent JSON:

```powershell
python scripts/configure_openai.py --config "<private-retail-agent.json>" --key-file "<private-key.txt>" --output "runtime-config-private/retail-agent-openai.json"
```

The helper never overwrites a file or uploads anything. For local use, replace
`--config` with `--env-file "<existing-local.env>"` and use an `.env` output file.
It preserves the other settings. The output directory is excluded from Git and images.

For this demo, `runtime-config-private/openai-demo.env` is a private key-only handoff
file prepared locally (it contains no OCI credentials). Give that file to the
deployment operator through a private channel, never commit it. They can use it as
the helper's `--key-file` on their computer, together with their deployment's private
Retail-agent JSON. Once applied on the server, every authorized demo user can select
OpenAI from any computer; their browser needs no API key or local configuration file.
Source code alone intentionally does not include the credential.

## Run this checkout locally

On the current laptop a private local configuration has been prepared at
`runtime-config-private/local-openai.env`. From this repository, start the backend:

```powershell
.\scripts\start-chat-local.ps1 -EnvFile ".\runtime-config-private\local-openai.env" -PythonExe "..\retail-inventory-agent\.venv\Scripts\python.exe"
```

In a second terminal from the repository:

```powershell
cd frontend
& "$env:USERPROFILE\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" .\node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5173 --strictPort
```

This laptop uses the existing bundled Node runtime because `npm.cmd` is not on
its PowerShell PATH. On another machine with Node/npm installed, the equivalent
command is `npm run dev -- --host 127.0.0.1 --port 5173 --strictPort`.

Open http://127.0.0.1:5173. If an older backend is running on port 8000, stop that
backend before starting this checkout. Sign in as a supplier, ask a delivery
question before registration, register an active compatible Custom API, and ask
again in the same chat. In retail chat, select OpenAI, load/choose a text model,
and verify the response metrics show the chosen provider/model.

## Deployment compatibility

Reviewed `Srichand-Oracle/Deployment_Automation` branch
`feature/retail-distributor-side-by-side`, commit
`65347b23a5d55e1c8d979532ba2421fdcc925f03`.

Its standard source build accepts `deployment/source-build.json` and the current
Dockerfiles. Its generated settings, hosted proxy registry routes, shared Oracle
delivery connection storage, guardrails and regional preflight pass the Retail
compatibility check with synthetic credentials. The branch already provisions
`DELIVERY_CONNECTIONS`; these changes require no cleanup or schema additions.

Build the five services from the approved new Retail commit when it is published.
The dashboard's separate two-application Distributor integration update does not
replace supplier-agent/MCP, and requires its own integration-capable source; it
is not the deployment path for these supplier routing changes.

The dashboard does not provision OpenAI credentials automatically. Configure the
key on the Retail agent through private runtime configuration and restart/update
that service. Do not silently overwrite an existing dashboard-managed config
object: deployment recovery tracks its hash. Prepare a new private configuration
object/revision and point the Retail runtime at it through the operator's existing
deployment process, preserving all other fields. No dashboard source-code change
is required, but that runtime secret configuration step is required for hosted OpenAI.

No cloud deployment is performed by the compatibility checks. The local key does
not travel with a Git push or source build.
