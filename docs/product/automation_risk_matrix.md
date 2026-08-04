# Automation Risk Matrix

| Action | Default level | Preconditions | Approval | Rollback/verification |
|---|---|---|---|---|
| Read Amazon/catalog/order/inventory data | Automatic | Valid tenant connection and rate limit | None | Cursor/audit reconciliation |
| Import cost CSV dry-run | Automatic | File validation and tenant scope | None | No persistent side effect |
| Commit validated cost version | Single approval for initial rollout | Dry-run checksum and error-free rows | Operator/admin | Supersede with new version; never mutate history |
| Calculate profit snapshot | Automatic | Deterministic inputs | None | Recalculate with versioned inputs |
| Create internal operational task | Automatic | Deduplication key and assignee policy | None | Close/cancel internal task |
| Generate report/notification/draft | Automatic | No PII leakage and evidence links | None | Regenerate/retract internal artifact |
| Small single-SKU price proposal | Recommendation only in first phase | Complete fresh profit data | Approval before future execution | Before snapshot and post-change margin |
| Small Listing copy edit | Single approval | Evidence, prohibited-claim check | Approver != requester | Restore prior content version |
| Low-risk ad budget adjustment | Single approval, later phase | Advertising metrics and spend cap | Advertising approver | Restore prior budget |
| Bulk price/Listing change | Two-person approval | Impact preview, canary and kill switch | Two distinct approvers | Batch rollback |
| Minimum margin or automation-policy change | Two-person approval | Owner authorization | Two distinct approvers | Restore prior policy version |
| Refund/payment reversal | Permanently prohibited | None | Not approvable by Agent workflow | Human/platform process only |
| Autonomous order cancellation | Permanently prohibited | None | Not approvable | Human/platform process only |
| Delete Listing or mass zero inventory | Permanently prohibited by default | Exceptional manual process outside Agent | Not automatic | Formal incident/change process |
| Customer PII access without explicit role/RDT | Permanently prohibited | None | Not approvable | Security incident process |
| Cross-tenant operation | Permanently prohibited | None | Not approvable | Security incident process |

Tenant automation policies may only become stricter than permanent platform safeguards. Initial commercial release keeps all Amazon writes disabled behind feature flags.
