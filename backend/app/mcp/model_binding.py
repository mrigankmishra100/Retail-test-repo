"""Only AI-dependent capabilities need a model configured on the MCP worker."""

# Other tools perform model-independent reads or verified state transitions,
# except answer_supplier_question, which always uses the server-owned supplier
# baseline. It accepts no caller-selected model or provider credentials.
# External credentials stay on the Retail worker. Its graph prepares AI drafts
# there; delegating an AI-dependent tool with an unknown profile must still fail.
MODEL_BOUND_TOOLS = frozenset({
    "prepare_workflow_case", "search_policy_documents", "query_retail_data",
})


def model_binding_for_tool(name, selection):
    if name in MODEL_BOUND_TOOLS and isinstance(selection, dict):
        return {key: selection[key] for key in ("profile", "model_id")}
    return None
