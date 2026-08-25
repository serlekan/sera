# Historical Workflow Bootstrap Exceptions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a fail-closed, machine-readable exception that lets documented historical tasks with no truthful builder handoff continue through SERA's existing review, gate, and seal states.

**Architecture:** Add a read-only validator beside packet integrity in `core.py`; it binds the exception to the current review packet's recorded implementation identity and reuses existing packet/coverage checks. `controller.next_action` consults that validator only when `packet-build.md` is missing, exposes its audit state, and otherwise retains the existing state machine and packet precedence.

**Tech Stack:** Python 3.11+, standard-library JSON/path handling, Git-backed SERA identity helpers, pytest/unittest integration tests.

## Global Constraints

- `no_fabricated_evidence` must be exactly the Boolean `true`.
- Never create `packet-build.md`, builder provenance, builder timestamps, context records, logs, or ledger evidence for an exception.
- Exception identity binds to the review packet's task-artifact HEAD/tree; existing packet validation separately binds that packet to the checkout.
- `future_workflow_required` must exactly equal `builder`, `packet`, `independent_review`, `gate`, `seal` in that order.
- An exception is never a workflow stage and introduces no `gate` action.
- Repositories with no exception retain existing behavior.
- A present builder packet remains authoritative; an exception file cannot override valid, stale, or invalid builder-packet handling.

---

## File Structure

- Create `tests/test_bootstrap_exception.py`: real-Git regression fixture and all accepted, invalid, ignored, and backward-compatible controller cases.
- Modify `src/sera/core.py`: schema constants plus the read-only `bootstrap_exception_state` validator.
- Modify `src/sera/controller.py`: consult the validator at the missing-builder branch and expose audit state.
- Modify `docs/CONTROLLER.md`: machine-readable schema, validation order, response state, and controller precedence.
- Modify `docs/WORKFLOW.md`: operator-facing explanation that missing history remains explicitly missing.

### Task 1: Core exception schema and integrity validator

**Files:**
- Create: `tests/test_bootstrap_exception.py`
- Modify: `src/sera/core.py:65-97`
- Modify: `src/sera/core.py:1149-1260`

**Interfaces:**
- Consumes: `packet_state(root, task_dir, "review", task, state_fingerprint)`, `read_packet_provenance(task_dir, "review")`, `task_review_coverage(root, task, max_chars)`, `task_fingerprint(root, task_dir)`, and `load_config(root)`.
- Produces: `BOOTSTRAP_EXCEPTION_INVALID`, `BOOTSTRAP_EXCEPTION_NOT_APPLICABLE`, `BOOTSTRAP_EXCEPTION_AUDIT_MESSAGE`, and `bootstrap_exception_state(root: Path, task_dir: Path, task: dict[str, Any], state_fingerprint: str | None = None) -> dict[str, Any]`.

- [ ] **Step 1: Create the real-repository fixture and write failing core tests**

  Create `tests/test_bootstrap_exception.py` with a temporary Git repository that:

  1. initializes SERA and commits a baseline;
  2. creates an assured/high task owning `src/app.py`;
  3. commits an implementation after task creation;
  4. generates only `packet-review.md` and its provenance;
  5. writes exception JSON using the review packet's literal `repository_identity`.

  The helper must write only the exception under test:

  ```python
  FUTURE_WORKFLOW = ["builder", "packet", "independent_review", "gate", "seal"]

  def write_exception(
      self,
      task_dir: Path,
      *,
      omit: tuple[str, ...] = (),
      **overrides: object,
  ) -> Path:
      identity = read_packet_provenance(task_dir, "review")["repository_identity"]
      payload = {
          "schema_version": 1,
          "type": "historical_workflow_bootstrap_exception",
          "reason": "Task predates native builder handoff capture.",
          "missing_stage": "builder_handoff",
          "no_fabricated_evidence": True,
          "implementation_head_sha": identity["head_sha"],
          "implementation_tree_sha": identity["head_tree_sha"],
          "future_workflow_required": FUTURE_WORKFLOW,
      }
      payload.update(overrides)
      for field in omit:
          payload.pop(field, None)
      path = task_dir / "bootstrap-exception.json"
      path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
      return path
  ```

  Add focused tests whose expectations are hand-derived literals:

  ```python
  def test_valid_exception_is_accepted_and_preserves_missing_builder_history(self) -> None:
      task_dir = self.historical_task()
      self.write_exception(task_dir)
      before_context = (task_dir / "context-ledger.jsonl").read_bytes()

      validator = getattr(core, "bootstrap_exception_state", None)
      self.assertTrue(callable(validator), "bootstrap exception validator is missing")
      state = validator(self.root, task_dir, load_task(task_dir))

      self.assertTrue(state["exists"])
      self.assertTrue(state["applicable"])
      self.assertTrue(state["accepted"])
      self.assertEqual(state["validation_errors"], [])
      self.assertIn("explicitly preserved as missing", state["audit_message"])
      self.assertFalse((task_dir / "packet-build.md").exists())
      self.assertFalse((task_dir / "packet-build.provenance.json").exists())
      self.assertEqual((task_dir / "context-ledger.jsonl").read_bytes(), before_context)
  ```

  ```python
  def test_exception_identity_must_match_review_packet_artifact(self) -> None:
      task_dir = self.historical_task()
      current = git_head_identity(self.root)
      self.write_exception(task_dir, implementation_head_sha="0" * len(current["head_sha"]))

      state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))

      self.assertFalse(state["accepted"])
      self.assertEqual(state["reason"], "bootstrap_exception_invalid")
      self.assertIn("implementation_head_mismatch", state["validation_errors"])
  ```

  Add a parallel tree-mismatch case. Add a table-driven strict-Boolean/schema test covering `False`, `"true"`, `1`, `"yes"`, and omission; every row must return `bootstrap_exception_invalid` and include `no_fabricated_evidence_invalid`. Add exact-list failures for missing, reordered, and extended `future_workflow_required`. Add malformed JSON and non-empty-reason failures.

  Add a task-artifact binding regression: rewrite review provenance to a different HEAD while leaving the exception equal to current checkout HEAD. Assert rejection because `review_packet_state` is not current; this proves matching a moving checkout alone is insufficient.

  Add an orphan-provenance regression: create `packet-build.provenance.json` without `packet-build.md`, then assert `builder_provenance_present` and `bootstrap_exception_invalid`.

- [ ] **Step 2: Run the core tests and verify the RED state**

  Run:

  ```text
  pytest tests/test_bootstrap_exception.py -q
  ```

  Import the `sera.core` module rather than importing the new symbol directly,
  and have the first test assert that `getattr(core, "bootstrap_exception_state", None)`
  is callable before invoking it. Expected: the test fails at that assertion
  because the behavioral entry point is absent; collection completes without
  an import or fixture error.

- [ ] **Step 3: Add schema constants and the minimal read-only validator**

  In `src/sera/core.py`, add:

  ```python
  BOOTSTRAP_EXCEPTION_SCHEMA_VERSION = 1
  BOOTSTRAP_EXCEPTION_TYPE = "historical_workflow_bootstrap_exception"
  BOOTSTRAP_EXCEPTION_INVALID = "bootstrap_exception_invalid"
  BOOTSTRAP_EXCEPTION_NOT_APPLICABLE = "bootstrap_exception_not_applicable"
  BOOTSTRAP_EXCEPTION_REQUIRED_WORKFLOW = (
      "builder",
      "packet",
      "independent_review",
      "gate",
      "seal",
  )
  BOOTSTRAP_EXCEPTION_AUDIT_MESSAGE = (
      "Builder handoff history is unavailable and has been explicitly preserved as missing. "
      "Workflow progression continues under a documented bootstrap exception. "
      "This does not assert that the builder stage occurred."
  )
  ```

  Implement `bootstrap_exception_state` beside `packet_state`. Its stable output shape is:

  ```python
  {
      "exists": bool,
      "applicable": bool,
      "accepted": bool,
      "reason": str | None,
      "validation_errors": list[str],
      "missing_stage": str | None,
      "implementation_identity": {
          "head_sha": str | None,
          "head_tree_sha": str | None,
      },
      "review_packet": {"exists": bool, "current": bool, "reason": str | None} | None,
      "coverage_complete": bool | None,
      "audit_message": str | None,
  }
  ```

  Validation order must be deterministic:

  1. return `exists: false` without reading any other artifact when no exception exists;
  2. if `packet-build.md` exists, return `applicable: false`, `reason: bootstrap_exception_not_applicable`; the controller must not consult exception validity;
  3. parse the JSON as an object, recording `exception_unreadable` for malformed/non-object content;
  4. validate every required field, using `type(value) is int` for schema version and `value is True` for `no_fabricated_evidence`;
  5. reject orphaned builder provenance with `builder_provenance_present`;
  6. recompute review packet state with the current task fingerprint;
  7. recompute coverage and require `coverage_complete is True`;
  8. compare exception identity to `read_packet_provenance(...)["repository_identity"]`, never directly to an unbound checkout value.

  Catch JSON, filesystem, and SERA validation failures and turn them into validation errors; a present exception must never raise its way around fail-closed controller behavior.

- [ ] **Step 4: Run core tests and verify GREEN**

  Run:

  ```text
  pytest tests/test_bootstrap_exception.py -q
  ```

  Expected: all core validator tests pass. Confirm the fixture still contains no builder packet/provenance and that the context ledger byte snapshot is unchanged.

- [ ] **Step 5: Commit the core validator**

  ```text
  git add src/sera/core.py tests/test_bootstrap_exception.py
  git commit -m "feat: validate historical workflow bootstrap exceptions"
  ```

### Task 2: Controller precedence and audit visibility

**Files:**
- Modify: `tests/test_bootstrap_exception.py`
- Modify: `src/sera/controller.py:12-35`
- Modify: `src/sera/controller.py:203-335`

**Interfaces:**
- Consumes: `bootstrap_exception_state(...)`, `BOOTSTRAP_EXCEPTION_INVALID`, and existing `build_packet_state`/`review_packet_state` results.
- Produces: `next_action(...)` response field `bootstrap_exception`; invalid response `state == "invalid"` and `next_action == "bootstrap_exception_invalid"`; accepted response continues using existing actions.

- [ ] **Step 1: Write failing controller regression tests**

  Extend the real fixture with `accept_review` at the independent stage, bound to `git_head_identity(self.root)`. Add the four required controller cases:

  ```python
  def test_accepted_exception_continues_to_existing_gate_dispatch(self) -> None:
      task_dir = self.reviewed_historical_task()
      self.write_exception(task_dir)

      report = next_action(self.root, task_dir)

      self.assertEqual(report["state"], "dispatch_review")
      self.assertEqual(report["next_action"], "dispatch_review")
      self.assertIn("gate", report["reason"])
      self.assertTrue(report["bootstrap_exception"]["accepted"])
  ```

  ```python
  def test_identity_mismatch_fails_closed_in_controller(self) -> None:
      task_dir = self.reviewed_historical_task()
      self.write_exception(task_dir, implementation_tree_sha="0" * 40)

      report = next_action(self.root, task_dir)

      self.assertEqual(report["state"], "invalid")
      self.assertEqual(report["next_action"], "bootstrap_exception_invalid")
  ```

  ```python
  def test_false_no_fabricated_evidence_fails_closed_in_controller(self) -> None:
      task_dir = self.reviewed_historical_task()
      self.write_exception(task_dir, no_fabricated_evidence=False)

      report = next_action(self.root, task_dir)

      self.assertEqual(report["state"], "invalid")
      self.assertEqual(report["next_action"], "bootstrap_exception_invalid")
  ```

  ```python
  def test_no_exception_preserves_packet_missing_behavior(self) -> None:
      task_dir = self.reviewed_historical_task()

      report = next_action(self.root, task_dir)

      self.assertEqual(report["state"], "build_packet")
      self.assertEqual(report["next_action"], "build_packet")
      self.assertEqual(report["build_packet"]["reason"], "packet_missing")
  ```

  Add the clarification regression using a fresh task with a valid native builder packet and no implementation change. Write any exception file beside it, call `next_action`, and assert `dispatch_builder`, `bootstrap_exception.accepted is False`, and `bootstrap_exception.reason == "bootstrap_exception_not_applicable"`. This test catches any future branch that lets the exception override a real builder artifact.

  Add a stale/unbound builder packet variant and assert the pre-existing `build_packet` action and original packet reason remain authoritative.

- [ ] **Step 2: Run controller cases and verify RED**

  Run:

  ```text
  pytest tests/test_bootstrap_exception.py -q
  ```

  Expected: the new controller cases fail because `next_action` still returns `build_packet` for the accepted exception and exposes no `bootstrap_exception` field.

- [ ] **Step 3: Integrate the validator at the existing missing-builder branch**

  Import the validator and reason constant in `src/sera/controller.py`. Compute audit state once alongside packet states:

  ```python
  bootstrap_exception = bootstrap_exception_state(
      root,
      task_dir,
      task,
      state_fingerprint=result["fingerprint"],
  )
  builder_satisfied_by_exception = (
      build_packet_state["reason"] == "packet_missing"
      and bootstrap_exception["accepted"]
  )
  ```

  Replace only the builder-packet branch:

  ```python
  elif not build_packet_state["current"] and not builder_satisfied_by_exception:
      if build_packet_state["reason"] == "packet_missing" and bootstrap_exception["exists"]:
          action, command = "invalid", None
          reported_next_action = BOOTSTRAP_EXCEPTION_INVALID
          reason = BOOTSTRAP_EXCEPTION_INVALID
      else:
          action, command = "build_packet", "sera packet build"
          reported_next_action = action
          reason = {
              "packet_missing": "The task is specified but no builder handoff exists.",
              "packet_unbound": "The existing builder packet has no valid task binding and cannot be dispatched.",
              "packet_stale_contract": "The task contract changed after this builder packet was generated; regenerate before dispatch.",
          }.get(
              build_packet_state["reason"],
              "The builder packet is not current for this task contract.",
          )
  ```

  Preserve every earlier scope, coverage, and context guard. Preserve every later verification, stale-review, failed-review, missing-review, seal, and accepted branch. Default `reported_next_action` to `action` for all ordinary branches. Add `"bootstrap_exception": bootstrap_exception` to the returned JSON object.

  Ensure a present builder packet never enters the invalid-exception branch: its existing packet reason remains the controlling result whether the packet is current, stale, or unbound.

- [ ] **Step 4: Run focused and adjacent controller suites**

  Run:

  ```text
  pytest tests/test_bootstrap_exception.py tests/test_controller.py tests/test_packet_integrity.py tests/test_review_precedence.py -q
  ```

  Expected: all tests pass, including `dispatch_review` for the accepted exception and unchanged native builder-packet progression.

- [ ] **Step 5: Commit controller integration**

  ```text
  git add src/sera/controller.py tests/test_bootstrap_exception.py
  git commit -m "feat: continue historical tasks under explicit exceptions"
  ```

### Task 3: Historical exception documentation and full verification

**Files:**
- Modify: `docs/CONTROLLER.md:367-445`
- Modify: `docs/WORKFLOW.md:35-81`

**Interfaces:**
- Consumes: the final schema and `next_action` response shape from Tasks 1-2.
- Produces: operator and controller documentation; no runtime interface changes.

- [ ] **Step 1: Document the machine contract in `docs/CONTROLLER.md`**

  Add a “Historical workflow bootstrap exceptions” subsection after the builder/review packet progression discussion. Include:

  - the exact schema JSON;
  - strict Boolean and exact ordered-list validation;
  - review-packet task-artifact identity binding plus existing checkout binding;
  - missing-builder precedence and `bootstrap_exception_invalid` response;
  - `bootstrap_exception` audit-state response shape;
  - the rule that any existing builder packet keeps ordinary packet validation authoritative.

  Include the approved audit language verbatim:

  > Builder handoff history is unavailable and has been explicitly preserved as missing. Workflow progression continues under a documented bootstrap exception. This does not assert that the builder stage occurred.

- [ ] **Step 2: Document the human workflow boundary in `docs/WORKFLOW.md`**

  Add a short subsection between Execute and Review explaining that exceptions are only for pre-native historical tasks, preserve unavailable history as missing, do not claim the builder ran, and do not remove independent review, gate, or seal requirements. State explicitly that future work must use the native full workflow.

- [ ] **Step 3: Run documentation and diff checks**

  Run:

  ```text
  git diff --check
  ```

  Then inspect the complete diff and confirm it contains no writes to `packet-build.md`, provenance, context ledgers, logs, or evidence ledgers.

- [ ] **Step 4: Run the full requested verification from a clean command invocation**

  Run exactly:

  ```text
  pytest
  sera check
  sera next
  ```

  Read each exit code and full output. The source repository currently has no `.sera` runtime directory; do not initialize or fabricate a task merely to force the last two commands green. If that remains true, report their actual not-initialized result separately from the passing automated suite.

  Also run the focused historical workflow test with verbose names for audit evidence:

  ```text
  pytest tests/test_bootstrap_exception.py -v
  ```

- [ ] **Step 5: Review requirements and commit documentation**

  Confirm line by line:

  - valid exception progresses to existing `dispatch_review` gate handling;
  - identity mismatch fails closed;
  - false/missing/non-Boolean `no_fabricated_evidence` fails closed;
  - no exception preserves `build_packet`;
  - valid/stale/unbound builder packets ignore exception progression;
  - no fabricated artifacts are created;
  - docs state missing history remains missing;
  - all audit state remains visible.

  Commit only the intended documentation and any final test correction:

  ```text
  git add docs/CONTROLLER.md docs/WORKFLOW.md tests/test_bootstrap_exception.py
  git commit -m "docs: explain historical workflow bootstrap exceptions"
  ```

  Preserve the unrelated untracked `sera-0.4.2-release-notes.md` file.

---

Tasks 1-3 above record the implementation rejected at
`0e6939b2b9e5cbdec724c29aef2e08334d306b9d`. Task 4 is the approved correction
and supersedes their audit-message, builder-artifact-precedence, and exception-
applicability instructions wherever they conflict.

### Task 4: Enforce policy-authorized historical eligibility

**Files:**
- Modify: `tests/test_bootstrap_exception.py`
- Modify: `src/sera/core.py`
- Modify: `src/sera/controller.py`

**Interfaces:**
- Consumes: `.sera/config.json`, `task["id"]`, `task["created_at"]`,
  `task_baseline_identity(task)`, and the existing exception/packet validators.
- Produces: config key `historical_bootstrap_eligibility: list[dict[str, Any]]`,
  constants `HISTORICAL_BOOTSTRAP_ELIGIBILITY_SCHEMA_VERSION` and
  `HISTORICAL_BOOTSTRAP_ELIGIBILITY_TYPE`, and fail-closed validation errors
  `historical_eligibility_missing`, `historical_eligibility_registry_invalid`,
  and `historical_eligibility_mismatch`.

- [ ] **Step 1: Add failing eligibility and builder-conflict tests**

Add a test helper that writes one explicit registry entry to the existing test
repository's `.sera/config.json`:

```python
def register_historical_task(self, task_dir: Path, **overrides: object) -> None:
    task = load_task(task_dir)
    baseline = task["baseline_repository_identity"]
    entry = {
        "task_id": task["id"],
        "created_at": task["created_at"],
        "baseline_head_sha": baseline["head_sha"],
        "baseline_tree_sha": baseline["head_tree_sha"],
        "eligibility_type": "historical_builder_handoff_gap",
        "schema_version": 1,
    }
    entry.update(overrides)
    path = self.root / ".sera" / "config.json"
    config = json.loads(path.read_text(encoding="utf-8"))
    config["historical_bootstrap_eligibility"] = [entry]
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
```

Cover these consumer-visible results with real temporary Git repositories:

```python
def test_registered_historical_task_is_accepted(self) -> None:
    task_dir = self.historical_task(register=True)
    self.write_exception(task_dir)
    state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))
    self.assertTrue(state["accepted"])

def test_current_task_cannot_self_authorize_with_exception(self) -> None:
    task_dir = self.historical_task(register=False)
    self.write_exception(task_dir)
    state = core.bootstrap_exception_state(self.root, task_dir, load_task(task_dir))
    self.assertFalse(state["accepted"])
    self.assertIn("historical_eligibility_missing", state["validation_errors"])
```

Add a separate controller regression proving a future/current task with a
perfectly formed exception still returns `bootstrap_exception_invalid`, a
table-driven registry mismatch test for task ID, creation timestamp, baseline
HEAD, baseline tree, eligibility type, and schema version, an explicit empty
registry test, and builder packet/provenance conflict tests that expect invalid
instead of ordinary builder dispatch.

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```text
PYTHONPATH=src pytest tests/test_bootstrap_exception.py -q
```

Expected: failures show unregistered exceptions are still accepted, registry
identity is not checked, and an existing builder packet still dispatches.

- [ ] **Step 3: Implement exact registry matching and invalid precedence**

In `src/sera/core.py`, add strict registry constants and a read-only helper that
loads `config.get("historical_bootstrap_eligibility", [])`. The helper must
reject non-list registries, malformed entries, Boolean or wrong schema
versions, wrong eligibility types, duplicate matching task IDs, missing task
IDs, and any mismatch against the task's exact ID, creation timestamp, or
baseline HEAD/tree.

`bootstrap_exception_state` must call it after exception schema validation,
reject either builder artifact, retain all existing review packet and coverage
validation, and emit this exact accepted audit message:

```text
Historical eligibility confirmed by policy registry. Bootstrap exception records missing historical builder evidence. No builder stage is claimed to have occurred.
```

In `src/sera/controller.py`, make every present invalid exception return
`state: invalid`, `next_action: bootstrap_exception_invalid`, and
`reason: bootstrap_exception_invalid` before ordinary packet routing. Leave the
no-exception state machine unchanged.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```text
PYTHONPATH=src pytest tests/test_bootstrap_exception.py -q
```

Expected: all focused tests pass with no builder artifact, timestamp, log,
provenance, context, or ledger creation.

- [ ] **Step 5: Commit the correction**

```bash
git add src/sera/core.py src/sera/controller.py tests/test_bootstrap_exception.py
git commit -m "fix: authorize historical bootstrap exceptions by policy"
```

### Task 5: Document the authorization boundary and verify the exact tree

**Files:**
- Modify: `docs/CONTROLLER.md`
- Modify: `docs/WORKFLOW.md`

**Interfaces:**
- Consumes: the `historical_bootstrap_eligibility` config schema and accepted
  audit message implemented in Task 4.
- Produces: operator instructions that distinguish policy authorization from
  the exception's missing-evidence record.

- [ ] **Step 1: Update operator documentation**

Document the default-empty registry, its exact entry schema, exact task-identity
binding, no automatic authorization writes, builder-artifact conflict behavior,
and the rule that missing builder history remains missing rather than being
reconstructed or claimed.

- [ ] **Step 2: Run final verification**

```text
PYTHONPATH=src pytest tests/test_bootstrap_exception.py -q
PYTHONPATH=src pytest -q
python -m ruff check .
git diff --check 0e6939b2b9e5cbdec724c29aef2e08334d306b9d..HEAD
```

Expected: focused and full suites pass, Ruff reports `All checks passed!`, and
`git diff --check` exits zero without output.

- [ ] **Step 3: Commit documentation**

```bash
git add docs/CONTROLLER.md docs/WORKFLOW.md
git commit -m "docs: define historical bootstrap authorization registry"
```

- [ ] **Step 4: Capture exact review identity**

```bash
git rev-parse HEAD
git rev-parse 'HEAD^{tree}'
git status --short --branch
```

Expected: a clean feature branch with exact commit and tree values ready for a
new independent review.
