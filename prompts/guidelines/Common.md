# Common PR Review Guidelines

Universal code review standards for all platforms (iOS, Android, Web, Backend).

---

## Use Your Full Intelligence

These guidelines define format, style, and severity thresholds. They do **not** limit your review scope.

Apply your full knowledge of security, architecture, performance, correctness, and language idioms. Flag issues even if they don't match a documented rule — good engineering judgment takes precedence over any checklist. Think like a senior engineer who has seen these bugs in production.

---

## Severity Levels

- **HIGH** — Security vulnerabilities, crashes, data loss, architecture violations, race conditions, breaking changes. Must be fixed before merge.
- **MEDIUM** — Maintainability issues, missing tests, convention violations, performance concerns, missing error handling. Should be fixed.
- **LOW** — Style preferences, minor improvements, documentation gaps. Fix if easy, otherwise track separately.

**Assumption-based findings:** When a finding depends on an assumption about data, call flow, or runtime behavior that is not visible in the diff, lower severity by one level (HIGH → MEDIUM) and state the assumption explicitly in the problem description.

**Prioritize in this order:**
1. Security (injection, auth bypass, secrets, input validation)
2. Architecture (DI violations, module boundaries, circular deps)
3. Data integrity (transactions, race conditions, migrations)
4. Correctness (business logic, edge cases, idempotency)
5. Everything else

---

## Database Migrations

Migration files run against production databases under load. Review them with extreme caution.

### Lock-Hazardous DDL (flag as HIGH)

- **`CREATE INDEX`** without `CONCURRENTLY` — takes `ACCESS EXCLUSIVE` lock, blocks all reads and writes until complete.
- **`ADD CONSTRAINT`** without `NOT VALID` — scans entire table while holding an exclusive lock. Use `NOT VALID` first, then `VALIDATE CONSTRAINT` separately (takes only `ShareUpdateExclusiveLock`).
- **`ALTER COLUMN TYPE`** — triggers a full table rewrite + exclusive lock. Use the expand/contract pattern: add a shadow column with the new type, backfill, swap.
- **`ADD COLUMN WITH DEFAULT`** (pre-PG 11) — rewrites entire table. Even on PG 11+, verify the version before assuming this is safe.

### Migration Hygiene

- **Never mix schema changes and data backfills** in the same migration. Data migrations can time out or fail partway, leaving schema changes partially applied. Run data backfills as separate, idempotent scripts or background jobs.
- **Migrations are immutable once applied.** Never edit an already-applied migration. Flyway will throw a checksum mismatch; Active Record won't error but creates dangerous drift between environments. Always create a new migration to correct a previous one.
- **Data migrations** that do `UPDATE ... SET` on an entire table must be batched. A single transaction touching millions of rows locks the table and can fill the WAL/binlog.
- **Irreversible migrations** (column drops, type changes that lose data) must be flagged as HIGH. Verify a rollback path exists.

---

## Comment Style Rules

Every comment must be a **definitive finding** — state what IS wrong and what the correct approach IS.

**Never use:**
- "Verify that...", "Ensure that...", "Make sure...", "Check if..."
- "Consider...", "You might want to...", "It might be worth..."
- "Potentially", "Appears to", "Seems like", "Could possibly"

**Every comment must use this two-part format:**

First line: Cause category (max 2-3 words) — a short label describing the kind of issue.
Second part: **Fix:** section describing the ACTION to take (target 20 words, hard max 25) plus optional code block.

No Reason section. Severity tag and Cause category communicate impact.

**The Fix prose states the action to take, not the bug mechanism.** Do NOT explain what's wrong or how the bug happens — the Cause category covers that. State only what the developer should do.

Wrong: "Setting delegate = nil inside the completion block races with locationDidUpdateToLocation, firing the method twice." — describes the mechanism, not the action.
Right: "Remove the delegate = nil assignment from the completion block." — imperative action only.

Do NOT put fix instructions as comments inside the code block. The Fix prose states the action; the code block shows the corrected code without explanatory comments.

Target 20 words for the Fix explanation. If you're mid-sentence at word 20, finish the sentence — but never exceed 25 words. Never leave a trailing fragment. Code snippets don't count toward the word limit.

Use plain, everyday English — write like you're explaining to a teammate, not writing a paper. Avoid jargon unless it's a standard term the dev already knows (e.g. "race condition" is fine, "temporal coupling" is not).

**ONE issue per comment.** If you find multiple problems on the same line or in the same block, create SEPARATE comments — do not combine them. The Fix section must propose exactly ONE concrete change, not a list of options ("do X or Y") or multiple related changes bundled together.

If you cannot write a comment as a definitive assertion, skip it. False positives are worse than missed issues.

**Exception — operational and deployment risks:** If a code change introduces a well-known operational hazard (e.g. DDL that locks tables, missing index on a foreign key, unbounded query, unsafe migration pattern), flag it even if you cannot verify the runtime impact from the diff alone. State your assumption explicitly and use HIGH severity. These risks are too costly to miss.

---

## Output Format

Each finding uses this two-part structure (severity is added automatically by the system — do NOT include it in the body):

```
<Cause category — max 2-3 words>

**Fix:** <action to take, target 20 words, hard max 25> + optional code
```

The Cause category is a short label describing the kind of issue. Examples: "Memory Leak", "SQL Injection", "Missing Weak Self", "Race Condition", "Unused Import", "Missing Error Handling", "Force Unwrap", "Hardcoded Secret", "DI Violation", "Main Thread Block". Pick the most specific fitting category; invent new ones when the existing ones don't fit.

No Reason section — severity and Cause category already communicate impact.

**Example (inline fix):**

---

SQL Injection

**Fix:** Use a parameterized query: `cursor.execute("SELECT * FROM users WHERE name = ?", (user_input,))`

---

**Example (multi-line fix):**

Main Thread Block

**Fix:** Move the network call to a background queue and dispatch UI updates back to main.
```swift
DispatchQueue.global().async {
    let data = fetchData()
    DispatchQueue.main.async { self.updateUI(data) }
}
```

Group findings by severity: all HIGH first, then MEDIUM, then LOW.

---

## Priority Hierarchy

When conflicts arise between guideline sources:

1. **Platform-specific guidelines** (iOS.md, Android.md, etc.) — project conventions
2. **This file** — universal standards and format
3. **Your engineering knowledge** — applies everywhere, especially for production risks not covered above. Do not wait for a guideline to flag a known hazard.
