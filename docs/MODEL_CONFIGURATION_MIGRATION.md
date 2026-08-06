# Model Configuration Migration

OmniSell uses a provider-neutral OpenAI-compatible gateway. The primary model variables are:

```env
MODEL_BASE_URL=https://provider.example/v1
MODEL_NAME=approved-model-id
MODEL_API_KEY=from-secret-manager
```

The old `VLLM_BASE_URL`, `VLLM_MODEL_NAME`, and `VLLM_API_KEY` names are supported as deprecated aliases until `2.0.0`.

## Compatibility behavior

- New variables only: loaded normally.
- Legacy variables only: loaded with one deprecation warning; secrets are never included in the warning.
- Both sets with identical values: accepted with a deprecation warning.
- Both sets with conflicting values: startup fails closed and names the conflicting variables.
- Removal target: `2.0.0`. That release is blocked until all deployment environments, secrets, Compose/Kubernetes manifests, CI jobs and operator runbooks use `MODEL_*`.

## Migration checklist

1. Inventory every environment that still sets `VLLM_*`; record the owner without copying secret values.
2. Add the equivalent `MODEL_*` variables and verify startup with both sets carrying identical values.
3. Remove `VLLM_*` from that environment and run the provider-neutral gateway smoke test.
4. Confirm logs contain no deprecation warning and no secret values.
5. Before `2.0.0`, run a repository and deployment-config scan proving that legacy names remain only in the compatibility layer, migration tests and historical documentation.

Local developer `.env` files may still contain the deprecated names and can trigger one expected warning. They may contain credentials and must not be read, rewritten or committed as part of this migration.

The rename does not imply a provider change. Local Ollama, vLLM, hosted OpenAI-compatible services, and approved fallback providers all use the same generic contract.
