---
name: deep-research
description: Iterative deep research loop — discover, plan, execute, review, repeat until convergence, then write up. Produces plan.json, research_notes.md, and final_report.md.
version: 2.0.0
tags: [research, arxiv, literature-review, technical-analysis, survey, iterative]
related_skills: [arxiv, deep-research-training-data]
---

# Deep Research

## When to Use

User asks for a deep dive, literature review, landscape mapping, or systematic comparison on a technical topic.

## Setup

Create working directory and initial files:

```
~/research/<topic-slug>/
  plan.json
  research_notes.md
  final_report.md        # created in Phase 5
```

Initialize plan.json:
```json
{"topic": "", "revision": 0, "questions": []}
```

Initialize research_notes.md:
```markdown
# Research Notes: <TOPIC>
<!-- Append-only. Never delete earlier findings. -->
```

---

## Phase 1: Discovery

You are mapping a landscape. Do NOT go deep. Breadth only.

1. Craft 5 search queries appropriate to the topic. Each query should cover one of these intents:
   - **Overview** — surveys, introductions, "what is this field"
   - **History** — origins, foundational work, key figures
   - **Current** — latest developments, best current thinking, recent results
   - **Contention** — debates, criticisms, limitations, open questions
   - **Practice** — real-world usage, implementations, tools, how-to guides

   Tailor the queries to the domain. A technical ML topic will have arxiv papers and benchmarks. A humanities or business topic will have books, essays, and practitioner blogs. Use your judgment.

2. Run the 5 searches in parallel using `delegate_task` with 3 subagents (split the queries across them). Each subagent returns: a list of items, each with `{title, url, 1-line summary}`. No full extracts yet.

3. Collect all results. Deduplicate by URL. You now have a landscape list.

4. From the landscape list, identify:
   - 3-5 major themes or camps
   - Key terminology
   - Rough timeline (when did this start, what are the eras)
   - Who the major authors/groups are

5. Hold this in context. Do NOT write it anywhere yet — it feeds Phase 2.

---

## Phase 2: Planning

Turn the landscape into a hierarchical question tree.

1. Write 5-8 top-level questions. Cover ALL of these angles:
   - Problem definition: What problem does this solve? Why does it matter?
   - Taxonomy: What are the major approaches? How do they differ?
   - SOTA: What are the best current results? On what benchmarks?
   - Mechanisms: How do the key methods actually work? (formulations, algorithms)
   - Tradeoffs: What are the practical pros/cons of each approach?
   - Open problems: What's unsolved? Where is the field heading?
   - Practice: What should a practitioner actually use today?

2. Under each top-level question, add 2-4 sub-questions where the topic has known depth. Use the landscape from Phase 1 to inform these — if you saw 3 competing approaches, create sub-questions for each.

3. Assign hierarchical IDs: "1", "1.1", "1.2", "2", "2.1", etc.

4. Set all statuses to "pending".

5. Write plan.json:
```json
{
  "topic": "<topic>",
  "revision": 0,
  "questions": [
    {
      "id": "1",
      "question": "...",
      "status": "pending",
      "children": [
        {"id": "1.1", "question": "...", "status": "pending", "children": []}
      ]
    }
  ]
}
```

6. **Show the plan to the user. Wait for approval before proceeding.**

---

## Phase 3: Execution

Answer each pending question with evidence. Work depth-first through the tree. Prefer to use the built-in web search, read/write file tools rather than writing new .py scripts. 

For each pending question:

1. **Search**: Run 1-3 web searches. Craft queries from the question text — be specific. Include year constraints if looking for recent work.

2. **Extract**: Pick the 2-3 most relevant URLs from search results. Run `web_extract` on them. For arxiv papers, use the PDF URL: `https://arxiv.org/pdf/XXXX.XXXXX`

3. **Verify**: Cross-check key claims across sources. Note when sources conflict.

4. **Record**: Append to research_notes.md in this exact format:

```markdown
## [<ID>] <Question text>

**Sources:**
- [<Title>](<URL>) — <1-line summary of what this source contributes>
- [<Title>](<URL>) — <1-line summary>

**Findings:**
- <Key fact 1> (source: <short ref>)
- <Key fact 2> (source: <short ref>)
- <Contradiction>: <Source A> says X, but <Source B> says Y

**Follow-up questions:**
- <New question discovered during research, or "None">
```

**Parallelism**: Group 2-3 top-level question groups and research them simultaneously using `delegate_task`. Each subagent gets a top-level question AND all its children — related sub-questions are best researched together since the sources overlap. Pass each subagent the full question list and the exact output format above. The subagent searches, extracts, and returns formatted findings for all assigned questions. You then append all results to research_notes.md yourself.

**Subagent prompt template**: "Research these questions about [TOPIC] and return findings in the exact format below. TOP-LEVEL: [ID] Question. SUB-QUESTIONS: [ID] Question, [ID] Question... For EACH question: 1. Search: run 1-2 targeted web searches. 2. Extract: web_extract on 2-3 most relevant URLs. 3. Return in this format: [paste the format above]"

**Appending notes**: Use `execute_code` with `hermes_tools.patch` or `hermes_tools.write_file` to append subagent results to research_notes.md. Do NOT use `read_file` then manual editing — the line-number format causes issues with JSON/markdown manipulation.

**Pace**: Do one batch of 2-3 top-level groups, then proceed to Phase 4 Review. Do NOT execute all questions before reviewing.

---

## Phase 4: Review

Update the plan based on what you learned.

1. Read plan.json and the latest entries in research_notes.md.

2. For each question you just answered:
   - Set status to "done"

3. Check Follow-up questions from the notes. For each:
   - If it's substantial and not already covered: add it as a child question with status "pending"
   - If it's minor or already covered: skip it

4. Check remaining pending questions:
   - If a pending question is now answered by findings from another question: mark "done"
   - If a pending question turned out to be irrelevant: mark "dropped"

5. Increment revision number.

6. Write updated plan.json. Use `execute_code` for all plan.json manipulation — JSON parsing, status updates, counting, and convergence checks in one script. Do NOT read plan.json with `read_file` and try to parse it (line-number format breaks JSON parsing).

### Convergence Check

Count: new questions added this revision, and remaining pending questions.

**Continue** → go back to Phase 3 if:
- There are pending questions remaining
- More than 1 new question was added this revision

**Stop** → proceed to Phase 5 if:
- All questions are done or dropped
- 0-1 new questions were added (the plan has stabilized)
- You've hit revision 6 (hard cap — wrap up with what you have)

---

## Phase 5: Write-Up

Convert raw notes into a structured report.

1. Read all of research_notes.md.

2. Organize findings into a logical narrative. The section order should follow the topic's natural structure, NOT the question numbering.

3. Write final_report.md. Adapt these section templates to fit the topic — rename, merge, split, or reorder as the material demands. The structure should serve the narrative, not the other way around. These are starting points, not a rigid template:

```markdown
# <Topic>: Deep Research Report

## Executive Summary
3-5 sentences. What is this field, what's the current state, what should the reader know.

## Background & Motivation
Why this problem matters. Historical context. Key definitions.

## Taxonomy of Approaches
Major categories of methods. Use a comparison table if there are 3+ approaches:
| Approach | Key Idea | Strengths | Weaknesses | Representative Work |

## State of the Art
Best current results. Benchmarks. Key papers with dates and venues.

## How It Works
Technical details of the 2-3 most important methods. Formulations, algorithms, architectures.

## Open Problems & Future Directions
What's unsolved. Active debates. Emerging trends.

## Practical Recommendations
Decision framework: "If you need X, use Y because Z."
Which codebases to start from. Compute requirements.

## References
All URLs from research_notes.md, deduplicated, organized by topic.
```

4. Present final_report.md to the user.

---

## Synthetic Data Generation (for GRPO training)

This skill's output maps directly to training data for deep research models (e.g., DeepResearch Bench format):

- **Input**: the prompt (research query)
- **Output**: final_report.md (the article with citations)
- **Rubric**: one LLM call per prompt generates task-specific scoring criteria across 4 dimensions (comprehensiveness, insight, instruction-following, readability) with weighted sub-criteria. Sample 5x and average weights for stability.
- **Reward signal**: LLM-as-judge scores the report against the rubric. Citation accuracy is checked programmatically (scrape URL, ask "does this page support this claim?").
- **Format**: `{"id": "...", "prompt": "...", "article": "..."}`

Reference benchmark: https://github.com/Ayanami0730/deep_research_bench/ (100 PhD-level tasks, RACE + FACT scoring, Gemini as judge).

## Tips

- Conference papers (NeurIPS, ICML, ICLR) > workshop > preprints. Always note venue + year.
- Check for withdrawn/retracted papers before citing.
- If user gives a time/depth constraint, reduce sub-questions per top-level question. Never skip phases.
- research_notes.md is append-only. Never delete earlier findings.
- The most valuable output is the taxonomy + decision framework, not a list of papers.
- Typical convergence: 2-3 iterations (we saw 2 iterations on a non-alcoholic cocktails topic with 25 questions).
- Phase 3 parallelism works well with delegate_task batches of 3 — each subagent handles a top-level question + its children.
- For research_notes.md appending via execute_code: use write_file with string concatenation, not patch (the file gets large fast and patch gets slow).
- When generating research prompts at scale, match real-world distribution: ~16% short open-ended (<15 words), ~35% medium, ~49% detailed. Include non-STEM topics (entertainment, food, business, sports). Don't make every prompt a multi-sentence PhD question.

## Pitfalls (from trial runs)

- **read_file returns line-numbered content** (`  1|text`). Never parse plan.json via `read_file` + `json.loads` — it will fail. Always use `execute_code` with `hermes_tools.read_file` which returns clean content, or use `terminal("cat file")`.
- **Subagents need the exact output format in their prompt.** If you just say "research this topic," they'll return unstructured prose. Paste the markdown template into the subagent goal.
- **Bundle parent + children for subagents.** Don't send individual sub-questions as separate subagent tasks — a subagent researching "production methods" will naturally find answers to "vacuum distillation" and "fermentation" children in the same sources.
- **Most follow-up questions are minor.** During review, be aggressive about skipping follow-ups that are tangential or that would be answered by existing pending questions. Only add follow-ups that represent genuine gaps.
- **Convergence happens fast.** In testing, 2 iterations (2 batches of 2-3 top-level groups) covered 25 questions. Don't over-plan for many iterations — the hard cap of 6 is rarely needed.
- **The write-up is the most token-intensive phase.** Read all of research_notes.md before writing. For large note files (400+ lines), this may require reading in chunks or using `execute_code` to extract just the findings bullets.
