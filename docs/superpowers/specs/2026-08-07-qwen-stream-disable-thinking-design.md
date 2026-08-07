# Qwen Stream Disable-Thinking Design

## Context

`LiteLLMBase.async_chat()` applies model-family request policies before it
constructs a non-streaming completion request. For Qwen3 models, that policy
adds `extra_body={"enable_thinking": false}`. The streaming method cleans the
generation configuration but does not apply or forward the request-level
policy, so Qwen3 streaming calls can still generate hidden reasoning content.
Enterprise WeChat uses this streaming path, which increases response latency.

## Scope

Change only the LiteLLM streaming request construction in
`rag/llm/chat_model.py`. The stream method will apply the existing
`_apply_model_family_policies()` helper to its request keyword arguments and
merge those arguments with the cleaned generation configuration before calling
`_construct_completion_args()`.

This preserves the current behavior for non-Qwen models and does not alter
assistant settings, retrieval, reranking, evidence selection, or UI behavior.

## Alternatives Considered

1. Apply the existing model-family policy in the streaming method. This is the
   selected approach because it keeps one source of truth and has the smallest
   behavioral scope.
2. Add a Qwen-specific conditional directly to the streaming method. This is
   shorter but duplicates policy logic and can drift from the non-stream path.
3. Change `_clean_conf()` to return both generation configuration and request
   arguments. This could unify the interface but expands the refactor beyond
   the bug being fixed.

## Request Flow

1. Clean the caller-provided generation configuration as today.
2. Apply model-family policies using the model name and provider.
3. Construct the streaming completion arguments from the cleaned generation
   configuration plus the policy-adjusted request arguments.
4. Call LiteLLM exactly as before.

For a Qwen3 model, the resulting streaming request must contain
`extra_body.enable_thinking == false`. Other model families receive only their
existing applicable policies.

## Error Handling

No new error path is introduced. Existing LiteLLM retry, timeout, and exception
handling remains unchanged. Caller-provided request arguments continue through
the same policy sanitizer used by non-stream requests.

## Testing

Add a focused asynchronous unit test around a concrete `LiteLLMBase` test
subclass. Mock the external LiteLLM call, exhaust the returned async stream, and
assert that a Qwen3 streaming request includes
`extra_body={"enable_thinking": false}`. The test must fail against the current
implementation because the argument is absent, then pass after the minimal
stream-path change.

Run the focused test file and the existing LLM unit-test directory. Apply Ruff
to the changed Python files.
