# SERA Governance & Architecture Version Handling v2.4 (Delta over v2.3)

**Status**: PROPOSED ARCHITECTURE BASELINE (v2.4) — Subject to Independent Architecture Review
**Predecessor**: [`../v2.3/sera-governance.md`](../v2.3/sera-governance.md) (accepted spec `bc3df742d16f3a49b53f417482ae328f8f053264`, activation `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`)
**Revision Scope**: How a PROPOSED architecture baseline coexists with the ACTIVE runtime baseline without changing SERA runtime behavior. No change to `.sera/` layout, multi-signal detection, or fail-closed enforcement.

---

## 1. Carry-Forward Declaration

All of [`../v2.3/sera-governance.md`](../v2.3/sera-governance.md) is carried forward **unchanged**:

- §1 Standardized 5-file `.sera/` repository layout — **unchanged**.
- §2 Deterministic Multi-Signal Detection & Fail-Closed Enforcement — **unchanged**.
- §3 Mandatory Review Gate Checklist — **unchanged**.

---

## 2. v2.4 Amendment — PROPOSED vs. ACTIVE baseline separation

> [!IMPORTANT]
> **`ARCHITECTURE_PROPOSAL_ISOLATION`**:
> A PROPOSED architecture baseline (this v2.4 corpus) has **no effect on SERA runtime behavior** until it is independently reviewed and explicitly activated. Concretely, while v2.4 is `PROPOSED`:
> - `ORYOL_ARCHITECTURE_BASELINE_VERSION` stays `2.3`.
> - `ORYOL_ARCHITECTURE_SPEC_SHA` stays `bc3df742d16f3a49b53f417482ae328f8f053264`.
> - `SERA_GOVERNANCE_VERSION` stays `0.4.2`.
> - Builder and reviewer packets embed the **v2.3** provenance triple, unchanged.
> - The v2.3 corpus (`knowledge/oryol/v2.3/`) remains the frozen accepted evidence and MUST have **zero diff** from the activation commit `78c349ba7e9b9954ac96bf3b18fbc0ded600bc23`.

### 2.1 Activation preconditions (informational; activation is NOT part of this task)

v2.4 may be promoted to `ACTIVE` only after, against **one exact frozen proposal commit + tree**:

1. GPT-6 Astra principal architecture review returns approval.
2. `anthropic/claude-opus-5` independent architecture review returns approval.
3. The configured OpenAI release gate returns approval.

All three MUST approve the **same** proposal identity. Activation then updates the three constants above, the v2.4 `ARCHITECTURE-BASELINE.md` status, and appends the SERA runtime active-version record — in a **separate** change, under the v2.3 → v2.4 activation procedure mirroring the v2.2 → v2.3 activation (`chore(oryol): activate architecture baseline v2.4`).

### 2.2 Governance test obligations

`tests/test_oryol_v2_governance.py` is extended (class `TestOryolV24ProposedArchitecture`) to prove, on every CI run:

- The v2.3 corpus is byte-for-byte frozen versus the activation commit.
- The v2.4 document registry is complete and internally consistent.
- ADR-003/004/005/006 each contain their required invariants and executable rules (structural assertions, not substring-only checks).
- The active SERA constants are still `2.3` / `bc3df742…` / `0.4.2` (proposal isolation holds).
