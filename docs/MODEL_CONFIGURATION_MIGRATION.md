# Model Configuration Migration

OmniSell uses a provider-neutral OpenAI-compatible gateway. The primary model variables are:

```env
MODEL_BASE_URL=https://provider.example/v1
MODEL_NAME=approved-model-id
MODEL_API_KEY=from-secret-manager
```

The old `VLLM_BASE_URL`, `VLLM_MODEL_NAME`, and `VLLM_API_KEY` names are supported as deprecated aliases for one release cycle.

## Compatibility behavior

- New variables only: loaded normally.
- Legacy variables only: loaded with one deprecation warning; secrets are never included in the warning.
- Both sets with identical values: accepted with a deprecation warning.
- Both sets with conflicting values: startup fails closed and names the conflicting variables.
- Removal target: the first release after all deployment environments and operator runbooks use `MODEL_*`.

The rename does not imply a provider change. Local Ollama, vLLM, hosted OpenAI-compatible services, and approved fallback providers all use the same generic contract.
