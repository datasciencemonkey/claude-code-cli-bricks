---
name: databricks-bdd-testing
description: "BDD testing with Python Behave and Databricks. Use when the user asks to set up BDD, create Gherkin feature files, write step definitions, scaffold a Behave project, run BDD tests, or test pipelines, Unity Catalog, jobs, or Apps using behavior-driven development."
---

# BDD testing with Python Behave

Set up and run Behavior-Driven Development test suites against Databricks using Python Behave (Gherkin). Generate feature files, step definitions, and test harnesses that call real Unity Catalog functions via the Statement Execution API.

## When to use

- User asks to "set up BDD", "scaffold Behave", "create Gherkin tests", or "add BDD to my project"
- User wants to test pipelines, Unity Catalog permissions, jobs, Apps, or SQL functions
- User has existing SQL rule functions and wants automated test coverage
- User asks to "write Given/When/Then tests" or "generate feature files"

## Quick start

### 1. Scaffold a Behave project

```bash
uv add --group test behave databricks-sdk httpx
```

Generate this directory structure:

```
features/
├── environment.py           # Databricks SDK setup, ephemeral schema lifecycle
├── steps/
│   ├── common_steps.py      # Shared: workspace connection, SQL execution, row counts
│   └── <domain>_steps.py    # Per-domain step implementations
├── catalog/                 # Feature files by domain
├── pipelines/
├── jobs/
└── sql/
behave.ini
```

### 2. Write a feature file

```gherkin
@compliance @smoke
Feature: Back-to-Back Promotion Compliance
  As a compliance officer
  I need to ensure products have a 4-week cooling period between promotions
  So that we comply with ACCC pricing guidelines

  Rule: Products must have a minimum 4-week gap between promotions

  Scenario: Product promoted in consecutive weeks violates cooling period
    Given a product was promoted in weeks 1, 2
    When I check for back-to-back promotions
    Then the result should be "FAILED"

  Scenario: Product with 5-week gap is compliant
    Given a product was promoted in weeks 1, 6
    When I check for back-to-back promotions
    Then the result should be "PASSED"

  Scenario Outline: Promotion gap validation
    Given a product was promoted in weeks <weeks>
    When I check for back-to-back promotions
    Then the result should be "<expected>"

    Examples: Various gaps
      | weeks      | expected |
      | 1, 2       | FAILED   |
      | 1, 5       | FAILED   |
      | 1, 6       | PASSED   |
      | 1, 6, 11   | PASSED   |
```

### 3. Implement step definitions

Step definitions call UC functions via the Statement Execution API:

```python
from __future__ import annotations

from behave import given, when, then
from behave.runner import Context


@given("a product was promoted in weeks {weeks}")
def step_promo_weeks(context: Context, weeks: str) -> None:
    context.promo_weeks = [int(w.strip()) for w in weeks.split(",")]


@when("I check for back-to-back promotions")
def step_check_b2b(context: Context) -> None:
    weeks = sorted(context.promo_weeks)
    if not weeks:
        context.result = "PASSED"
        return

    last = weeks[-1]
    prev_promos = [False, False, False, False]
    for w in weeks[:-1]:
        gap = last - w
        if 1 <= gap <= 4:
            prev_promos[gap - 1] = True

    args = ", ".join(["TRUE"] + [str(p).upper() for p in prev_promos])
    violation = call_rule(f"check_back_to_back_promo({args})")
    context.result = "FAILED" if violation else "PASSED"


@then('the result should be "{expected}"')
def step_result_is(context: Context, expected: str) -> None:
    assert context.result == expected, f"Expected '{expected}' but got '{context.result}'"
```

### 4. The test harness: `call_rule()`

The core pattern: call real UC functions via the Statement Execution API. No local PySpark needed.

```python
from databricks.sdk import WorkspaceClient

def call_rule(expr: str):
    """Execute a SQL expression against the warehouse and return the scalar result."""
    ws = WorkspaceClient()
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")

    # Auto-qualify unqualified function names
    if "." not in expr.split("(")[0]:
        func_name = expr.split("(")[0].strip()
        expr = expr.replace(func_name, f"{catalog}.{schema}.{func_name}", 1)

    sql = f"SELECT {expr} AS result"
    response = ws.statement_execution.execute_statement(
        warehouse_id=warehouse_id,
        statement=sql,
        wait_timeout="30s",
    )
    raw = response.result.data_array[0][0]
    return _coerce(raw)  # Convert "true"->True, "false"->False, numeric->int/float
```

### 5. Run tests

```bash
# All tests
uv run behave --format=pretty

# Smoke tests only
uv run behave --tags="@smoke" --format=pretty

# Specific feature
uv run behave features/catalog/permissions.feature

# Dry run (validate step coverage)
uv run behave --dry-run

# JUnit output for CI
uv run behave --junit --junit-directory=reports/ --format=progress
```

## Common patterns

### Pattern 1: Testing Unity Catalog SQL functions

SQL functions are the single source of truth. The same function runs in BDD tests and in the production pipeline.

```sql
-- sql/rules/check_back_to_back_promo.sql
CREATE OR REPLACE FUNCTION check_back_to_back_promo(
  is_promoted BOOLEAN,
  prev_promo_week_1 BOOLEAN,
  prev_promo_week_2 BOOLEAN,
  prev_promo_week_3 BOOLEAN,
  prev_promo_week_4 BOOLEAN
)
RETURNS BOOLEAN
RETURN
  is_promoted AND (
    COALESCE(prev_promo_week_1, FALSE) OR
    COALESCE(prev_promo_week_2, FALSE) OR
    COALESCE(prev_promo_week_3, FALSE) OR
    COALESCE(prev_promo_week_4, FALSE)
  );
```

The production pipeline calls the same function:

```sql
CREATE OR REFRESH MATERIALIZED VIEW compliance_results AS
WITH timeline_with_lags AS (
  SELECT *,
    LAG(is_promoted, 1) OVER w AS prev_promo_1,
    LAG(is_promoted, 2) OVER w AS prev_promo_2,
    LAG(is_promoted, 3) OVER w AS prev_promo_3,
    LAG(is_promoted, 4) OVER w AS prev_promo_4
  FROM silver_timeline
  WINDOW w AS (PARTITION BY product_id, location_id ORDER BY week_start)
)
SELECT
  check_back_to_back_promo(
    t.is_promoted, t.prev_promo_1, t.prev_promo_2,
    t.prev_promo_3, t.prev_promo_4
  ) AS b2b_violation
FROM timeline_with_lags t;
```

### Pattern 2: Ephemeral test schemas

Each test run creates an isolated schema, preventing cross-run contamination:

```python
# environment.py
def before_all(context):
    ws = WorkspaceClient()
    context.workspace = ws
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    context.test_schema = f"behave_test_{ts}"
    ws.statement_execution.execute_statement(
        warehouse_id=context.warehouse_id,
        statement=f"CREATE SCHEMA IF NOT EXISTS {context.catalog}.{context.test_schema}",
        wait_timeout="30s",
    )

def after_all(context):
    context.workspace.statement_execution.execute_statement(
        warehouse_id=context.warehouse_id,
        statement=f"DROP SCHEMA IF EXISTS {context.catalog}.{context.test_schema} CASCADE",
        wait_timeout="30s",
    )
```

### Pattern 3: Scenario Outlines for data-driven testing

```gherkin
Scenario Outline: Established price boundary coverage
  Given the promotion status is "<promoted>"
  And the regular price is $<current> with prior weeks $<w1>, $<w2>, $<w3>, $<w4>
  When I check the established price rule
  Then the result should be "<expected>"

  Examples: Various price histories
    | promoted | current | w1    | w2    | w3    | w4    | expected |
    | yes      | 10.00   | 10.00 | 10.00 | 10.00 | 10.00 | PASSED   |
    | yes      | 10.00   | 10.00 | 10.00 | 10.00 | 9.99  | FAILED   |
    | no       | 10.00   | 5.00  | 6.00  | 7.00  | 8.00  | PASSED   |
```

### Pattern 4: Pipeline integration tests

```gherkin
@integration @slow
Feature: Pipeline end-to-end verification
  Verify compliance rules through Bronze -> Silver -> Gold.

  Scenario: Single promotion with gap passes end-to-end
    Given a pipeline workspace connection
    And events for product "PIPE-001" with a 5-week gap between promotions
    When I push the events to the pipeline
    And I wait for Gold results
    Then the compliance status should be "PASSED"
```

### Pattern 5: Grant and permission testing

```gherkin
@catalog @smoke
Feature: Unity Catalog permissions
  Scenario: Grant SELECT on a table
    Given a table "customers" in the test schema
    When I grant SELECT on "customers" to group "readers"
    Then the group "readers" should have SELECT on "customers"
```

## Gherkin writing rules

**Declarative, not imperative.** Describe what the system should do, not UI clicks.

**One behavior per scenario.** Split scenarios that test multiple independent things.

**CRITICAL: Curly braces break step matching.** Behave's `parse` library treats `{anything}` as a capture group. Never use `{schema}.table` in feature text. Use short names like `"customers"` and resolve the schema in step code.

**Trailing colons for data tables.** When a step has a data table, the `:` is part of the step text. Pattern must be `@given('a table with data:')` not `@given('a table with data')`.

**Tag strategy:**

| Tag | Purpose | Typical runtime |
|-----|---------|----------------|
| `@smoke` | Critical path, fast | < 30s each |
| `@regression` | Thorough coverage | Minutes |
| `@integration` | Needs live workspace | Minutes |
| `@slow` | Pipeline/job execution | > 2 min |
| `@wip` | Work in progress, skip in CI | N/A |

## Makefile targets

```makefile
.PHONY: bdd bdd-smoke bdd-report

bdd:
	uv run behave --format=pretty

bdd-smoke:
	uv run behave --tags="@smoke" --format=pretty

bdd-report:
	uv run behave --junit --junit-directory=reports/ --format=progress
```

## Prerequisites

- Python 3.10+
- `uv` for package management
- `databricks-sdk` and `behave` (`uv add --group test behave databricks-sdk`)
- Authenticated Databricks CLI profile or environment variables
- A SQL warehouse (auto-discovered if not specified)

## Reference files

- [gherkin-patterns.md](references/gherkin-patterns.md) — Databricks-specific Gherkin patterns for UC, pipelines, jobs, Apps, SQL
- [step-library.md](references/step-library.md) — Reusable step definitions for all Databricks domains
- [environment-template.md](references/environment-template.md) — Complete environment.py with Databricks hooks

## Common issues

| Issue | Solution |
|-------|----------|
| **Undefined step** | Run `uv run behave --dry-run` to find unmatched steps |
| **Auth failure (401/403)** | Check `databricks auth profiles` or env vars |
| **WAREHOUSE_NOT_RUNNING** | Start the SQL warehouse or use auto-start |
| **SCHEMA_NOT_FOUND** | Verify `before_all` created the ephemeral schema |
| **Step match collision** | Behave imports all steps globally; use unique patterns |
| **Curly brace parse error** | Don't use `{schema}` in feature files; resolve in step code |

## External resources

- [Public plugin repo](https://github.com/dgokeeffe/databricks-bdd-tools) — Full Claude Code plugin with four skills
- [The Foundation of Modern DataOps](https://medium.com/dbsql-sme-engineering/the-foundation-of-modern-dataops-with-databricks-68e36f5d72e8) — DataOps testing principles
