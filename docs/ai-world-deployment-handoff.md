# AI World Retail demo: deployment handoff

Source branch: `R-I-Cloudworld` in `mrigankmishra100/Retail-test-repo`.

This release includes supplier delivery routing fixes, automatic session selection
when a supplier registers a Custom agent, optional bearer-token entry, shared
OpenAI model selection, a compact model dialog, and usage-aware cost displays.
It also includes concurrent model comparisons, corrected analytical-claim checks,
stable partial-result labels, scrollable comparison tables and removal of the
OCI application events browser. It retains `deployment/source-build.json`.
These updates add no tables or migrations beyond the existing delivery-connection
storage introduced in R-I-Cloudworld. That Oracle fallback still needs the grants
or owner setup documented in DEPLOYMENT.md.

## OpenAI configuration for the dashboard owner

**No dashboard change is needed for session-key entry.** When no shared key is
configured, the OpenAI picker asks the user for an API key, loads its models, and
uses the existing authenticated backend-to-Retail-agent connection. The key is
held in the Retail agent's session model store, not browser storage or Git. Users
must reconnect after session expiry or a server restart. The current branches
already support this backend contract; deploy the updated frontend for the picker.

The following setup is optional, for a shared key that users need not enter:

Keep `OPENAI_API_KEY` in private VM configuration, following the dashboard's existing
handling of deployment secrets. Include it only in the generated **retail-agent**
private runtime JSON (`APPLICATION_ROLE=retail-agent`). Setting the variable on
the dashboard VM alone does not pass it to an OCI hosted application.

The Retail code already accepts this optional setting and resolves shared OpenAI
selections on the Retail agent. It does not expose the key to browser users.
The source branch contains no API key; request the key separately through a
private channel. Baseline OCI model options remain available without this key.

For automated dashboard provisioning, add the setting to its private secret
capture/config-generation path. The reviewed dashboard's Langfuse loader accepts
only its Langfuse allowlist: do not simply insert `OPENAI_API_KEY` into that file.
Use a separate private setting and preserve it during recovery or later updates.
Keep the value out of public run records, logs, frontend configuration and build
arguments. Configuration receipts/hashes must match the generated object.

For a manual preparation step, this repository also provides a helper. Run from
the repository root on the deployment operator's machine:

```sh
python scripts/configure_openai.py --config "/private/retail-agent.json" --key-file "/private/openai-key.txt" --output "/private/retail-agent-openai.json"
```

Use that machine's private paths (quoted Windows paths work too). The key file
may contain the bare key or `OPENAI_API_KEY=<key>`. The input JSON must be the
existing Retail-agent runtime configuration; other settings are retained. The
output parent directory must exist, and the output file must not already exist.
The helper prints no key, makes no cloud calls, and never overwrites a file.

Apply the output through the dashboard's supported private configuration workflow
before activation, or as a new configuration revision for an existing deployment.
Do not overwrite a configuration object behind the dashboard's recorded hash.
Manual preparation needs no dashboard code change; automatic injection requires
the dashboard owner to wire the additional setting into its provisioning code.

## Two-stage deployment

Based on Deployment_Automation's `feature/retail-distributor-side-by-side` branch,
reviewed at commit `65347b23a5d55e1c8d979532ba2421fdcc925f03`:

1. Deploy all five services from this Retail branch. Optionally configure a shared
   OpenAI key on the Retail agent, or use session-key entry. Use the latest source commit so the
   Retail agent also includes the comparison guardrail fix.
2. Use `feature/retail-distributor-integration` for the second stage. It already
   includes this Retail update and preserves Distributor functionality. Supply
   its existing Distributor settings, tables and prepared knowledge as documented
   in that branch's `docs/distributor-integration.md`.
3. The second stage builds only a new backend and frontend. It reuses the first
   deployment's Retail agent, Supplier agent and MCP server, so any shared OpenAI key
   stays configured on the existing Retail agent. It does not need to be copied
   into the new frontend or gateway backend.

Deploying one source branch does not merge it into the other branch. If that
dashboard flow changes to recreate the Retail agent, propagate its private key
configuration to the replacement as well.

## Verification after deployment

- In retail chat, select OpenAI, load a compatible Responses/function-calling
  model, ask an inventory question, and check that the reported provider/model
  matches. Verify the key is not returned to the browser.
- Compare two models: verify that each result appears independently with the right
  model label, failures remain visible, and wide tables scroll inside the card.
  Model latency and answer quality can still differ; provider limits still apply.
- Exercise the existing replenishment workflow and explicit approval controls.
- In supplier chat, ask a delivery question before registration, register an
  active compatible Custom API with its optional token, then ask again in the
  same chat without refreshing. Check the direct delivery response.
- Repeat these checks from the second-stage frontend. Costs should appear only
  when complete usage and an estimate are available; no estimate is an expected
  state for models without configured pricing.

Source/configuration checks are not a live OCI deployment test. Build and exercise
the images and both hosted URLs as part of the event deployment.
