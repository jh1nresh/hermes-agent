---
name: github-code-review
description: Review code changes by analyzing git diffs, leaving inline comments on PRs, and performing thorough pre-push review. Uses GitHub MCP tools (mcp_github_*) as the primary interface, with git CLI for local diff operations.
version: 2.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [GitHub, Code-Review, Pull-Requests, Git, Quality, MCP]
    related_skills: [github-auth, github-pr-workflow]
---

# GitHub Code Review

Perform code reviews on local changes before pushing, or review open PRs on GitHub. This skill uses **GitHub MCP tools** (`mcp_github_*`) as the primary interface for all GitHub API interactions, with plain `git` for local diff operations.

## Prerequisites

- GitHub MCP server configured (provides `mcp_github_*` tools)
- Inside a git repository (for local diff operations)

---

## 1. Reviewing Local Changes (Pre-Push)

Local diffs use plain `git` — no API needed.

### Get the Diff

```bash
# Staged changes (what would be committed)
git diff --staged

# All changes vs main (what a PR would contain)
git diff main...HEAD

# File names only
git diff main...HEAD --name-only

# Stat summary (insertions/deletions per file)
git diff main...HEAD --stat
```

### Review Strategy

1. **Get the big picture first:**

```bash
git diff main...HEAD --stat
git log main..HEAD --oneline
```

2. **Review file by file** — use `read_file` on changed files for full context, and the diff to see what changed:

```bash
git diff main...HEAD -- src/auth/login.py
```

3. **Check for common issues:**

```bash
# Debug statements, TODOs, console.logs left behind
git diff main...HEAD | grep -n "print(\|console\.log\|TODO\|FIXME\|HACK\|XXX\|debugger"

# Large files accidentally staged
git diff main...HEAD --stat | sort -t'|' -k2 -rn | head -10

# Secrets or credential patterns
git diff main...HEAD | grep -in "password\|secret\|api_key\|token.*=\|private_key"

# Merge conflict markers
git diff main...HEAD | grep -n "<<<<<<\|>>>>>>\|======="
```

4. **Present structured feedback** to the user.

### Review Output Format

When reviewing local changes, present findings in this structure:

```
## Code Review Summary

### Critical
- **src/auth.py:45** — SQL injection: user input passed directly to query.
  Suggestion: Use parameterized queries.

### Warnings
- **src/models/user.py:23** — Password stored in plaintext. Use bcrypt or argon2.
- **src/api/routes.py:112** — No rate limiting on login endpoint.

### Suggestions
- **src/utils/helpers.py:8** — Duplicates logic in `src/core/utils.py:34`. Consolidate.
- **tests/test_auth.py** — Missing edge case: expired token test.

### Looks Good
- Clean separation of concerns in the middleware layer
- Good test coverage for the happy path
```

---

## 2. Reviewing a Pull Request on GitHub (MCP Tools)

### Step 1: Gather PR Context

Use MCP tools to get PR metadata, description, and changed files:

```
# Get PR details (title, author, description, branch, status)
mcp_github_pull_request_read(method="get", owner=OWNER, repo=REPO, pullNumber=PR_NUMBER)

# Get the diff
mcp_github_pull_request_read(method="get_diff", owner=OWNER, repo=REPO, pullNumber=PR_NUMBER)

# Get list of changed files with additions/deletions
mcp_github_pull_request_read(method="get_files", owner=OWNER, repo=REPO, pullNumber=PR_NUMBER)

# Get CI/CD status
mcp_github_pull_request_read(method="get_status", owner=OWNER, repo=REPO, pullNumber=PR_NUMBER)

# Get check runs (individual CI jobs)
mcp_github_pull_request_read(method="get_check_runs", owner=OWNER, repo=REPO, pullNumber=PR_NUMBER)
```

### Step 2: Read File Contents for Context

For each changed file, read the full file to understand the surrounding context:

```
# Read specific files from the PR branch
mcp_github_get_file_contents(owner=OWNER, repo=REPO, path="src/auth/login.py", ref="refs/pull/PR_NUMBER/head")
```

### Step 3: Check Out Locally (Optional — for running tests)

If you need to run tests or linters locally:

```bash
git fetch origin pull/PR_NUMBER/head:pr-PR_NUMBER
git checkout pr-PR_NUMBER

# Run tests
python -m pytest 2>&1 | tail -20

# Run linter
ruff check . 2>&1 | head -30
```

### Step 4: Get Existing Review Comments

Check what's already been discussed:

```
# Get review threads (grouped comments on code locations)
mcp_github_pull_request_read(method="get_review_comments", owner=OWNER, repo=REPO, pullNumber=PR_NUMBER)

# Get general PR comments
mcp_github_pull_request_read(method="get_comments", owner=OWNER, repo=REPO, pullNumber=PR_NUMBER)

# Get formal reviews (approvals, change requests)
mcp_github_pull_request_read(method="get_reviews", owner=OWNER, repo=REPO, pullNumber=PR_NUMBER)
```

### Step 5: Apply the Review Checklist (Section 3)

Go through each category systematically.

### Step 6: Submit a Formal Review with Inline Comments

Use the MCP review tools to submit findings:

**Create a pending review, add inline comments, then submit:**

```
# Step A: Create a pending review (omit "event" to keep it pending)
mcp_github_pull_request_review_write(
    method="create",
    owner=OWNER,
    repo=REPO,
    pullNumber=PR_NUMBER
)

# Step B: Add inline comments to the pending review
mcp_github_add_comment_to_pending_review(
    owner=OWNER,
    repo=REPO,
    pullNumber=PR_NUMBER,
    path="src/auth.py",
    line=45,
    body="🔴 **Critical:** User input passed directly to SQL query — use parameterized queries.",
    subjectType="LINE",
    side="RIGHT"
)

mcp_github_add_comment_to_pending_review(
    owner=OWNER,
    repo=REPO,
    pullNumber=PR_NUMBER,
    path="src/models/user.py",
    line=23,
    body="⚠️ **Warning:** Password stored without hashing. Use bcrypt or argon2.",
    subjectType="LINE",
    side="RIGHT"
)

# Step C: Submit the pending review
mcp_github_pull_request_review_write(
    method="submit_pending",
    owner=OWNER,
    repo=REPO,
    pullNumber=PR_NUMBER,
    event="REQUEST_CHANGES",  # or "APPROVE" or "COMMENT"
    body="## Hermes Agent Review\n\nFound 2 issues. See inline comments."
)
```

**Or submit a review directly (no pending step):**

```
# Approve
mcp_github_pull_request_review_write(
    method="create",
    owner=OWNER,
    repo=REPO,
    pullNumber=PR_NUMBER,
    event="APPROVE",
    body="LGTM! Code looks clean — good test coverage, no security concerns."
)

# Request changes
mcp_github_pull_request_review_write(
    method="create",
    owner=OWNER,
    repo=REPO,
    pullNumber=PR_NUMBER,
    event="REQUEST_CHANGES",
    body="Found a few issues — see inline comments."
)
```

### Step 7: Post a Summary Comment

Leave a top-level summary so the PR author gets the full picture:

```
mcp_github_add_issue_comment(
    owner=OWNER,
    repo=REPO,
    issue_number=PR_NUMBER,
    body="""## Code Review Summary

**Verdict: Changes Requested** (2 issues, 1 suggestion)

### 🔴 Critical
- **src/auth.py:45** — SQL injection vulnerability

### ⚠️ Warnings
- **src/models.py:23** — Plaintext password storage

### 💡 Suggestions
- **src/utils.py:8** — Duplicated logic, consider consolidating

### ✅ Looks Good
- Clean API design
- Good error handling in the middleware layer

---
*Reviewed by Hermes Agent*"""
)
```

### Step 8: Reply to Existing Comments

If the PR author responds to your review:

```
# Reply to a specific review comment
mcp_github_add_reply_to_pull_request_comment(
    owner=OWNER,
    repo=REPO,
    pullNumber=PR_NUMBER,
    commentId=COMMENT_ID,
    body="Good point! That approach works too."
)
```

### Step 9: Request Copilot Review (Optional)

For automated AI feedback before your review:

```
mcp_github_request_copilot_review(owner=OWNER, repo=REPO, pullNumber=PR_NUMBER)
```

### Step 10: Clean Up (if checked out locally)

```bash
git checkout main
git branch -D pr-PR_NUMBER
```

---

## 3. Review Checklist

When performing a code review (local or PR), systematically check:

### Correctness
- Does the code do what it claims?
- Edge cases handled (empty inputs, nulls, large data, concurrent access)?
- Error paths handled gracefully?

### Security
- No hardcoded secrets, credentials, or API keys
- Input validation on user-facing inputs
- No SQL injection, XSS, or path traversal
- Auth/authz checks where needed
- Use `mcp_github_run_secret_scanning` on changed files for automated secret detection

### Code Quality
- Clear naming (variables, functions, classes)
- No unnecessary complexity or premature abstraction
- DRY — no duplicated logic that should be extracted
- Functions are focused (single responsibility)

### Testing
- New code paths tested?
- Happy path and error cases covered?
- Tests readable and maintainable?

### Performance
- No N+1 queries or unnecessary loops
- Appropriate caching where beneficial
- No blocking operations in async code paths

### Documentation
- Public APIs documented
- Non-obvious logic has comments explaining "why"
- README updated if behavior changed

---

## 4. Pre-Push Review Workflow

When the user asks you to "review the code" or "check before pushing":

1. `git diff main...HEAD --stat` — see scope of changes
2. `git diff main...HEAD` — read the full diff
3. For each changed file, use `read_file` if you need more context
4. Apply the checklist above
5. Present findings in the structured format (Critical / Warnings / Suggestions / Looks Good)
6. If critical issues found, offer to fix them before the user pushes

---

## 5. PR Review Workflow (End-to-End with MCP Tools)

When the user asks you to "review PR #N", "look at this PR", or gives you a PR URL:

### Quick Reference

| Task | MCP Tool |
|------|----------|
| Get PR details | `mcp_github_pull_request_read(method="get")` |
| Get PR diff | `mcp_github_pull_request_read(method="get_diff")` |
| Get changed files | `mcp_github_pull_request_read(method="get_files")` |
| Get CI status | `mcp_github_pull_request_read(method="get_status")` |
| Get check runs | `mcp_github_pull_request_read(method="get_check_runs")` |
| Read file contents | `mcp_github_get_file_contents(ref="refs/pull/N/head")` |
| Get review threads | `mcp_github_pull_request_read(method="get_review_comments")` |
| Get PR comments | `mcp_github_pull_request_read(method="get_comments")` |
| Get reviews | `mcp_github_pull_request_read(method="get_reviews")` |
| Create pending review | `mcp_github_pull_request_review_write(method="create")` |
| Add inline comment | `mcp_github_add_comment_to_pending_review()` |
| Submit review | `mcp_github_pull_request_review_write(method="submit_pending")` |
| Add PR comment | `mcp_github_add_issue_comment()` |
| Reply to comment | `mcp_github_add_reply_to_pull_request_comment()` |
| Scan for secrets | `mcp_github_run_secret_scanning()` |
| Request Copilot review | `mcp_github_request_copilot_review()` |

### Decision: Approve vs Request Changes vs Comment

- **Approve** — no critical or warning-level issues, only minor suggestions or all clear
- **Request Changes** — any critical or warning-level issue that should be fixed before merge
- **Comment** — observations and suggestions, but nothing blocking (use when you're unsure or the PR is a draft)
