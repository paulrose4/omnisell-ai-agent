# Legacy AutoGen Demo

This directory contains the pre-production GroupChat experiment retained for historical and interview discussion only.

- It is not imported by `api`, `application`, `workflows`, `model_gateway`, or the production `agents` package.
- It is not installed as part of the runtime dependency set because `pyautogen` is intentionally optional.
- It has no authority to write Amazon, refund, reverse payments, cancel orders, approve actions, or mutate production business state.
- Current production analysis uses tenant-scoped bounded advisors and `PersistentAgentWorkflow`.

Run only in an isolated development environment with an explicitly installed compatible AutoGen version:

```powershell
python -m legacy.autogen_demo.group_chat "Review the supplied synthetic evidence"
```

All output from this demo is unverified internal analysis.
