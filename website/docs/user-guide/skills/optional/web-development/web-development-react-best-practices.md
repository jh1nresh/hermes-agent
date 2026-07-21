---
title: "React Best Practices — React/Next"
sidebar_label: "React Best Practices"
description: "React/Next"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# React Best Practices

React/Next.js performance rules from Vercel Engineering.

## Skill metadata

| | |
|---|---|
| Source | Optional — install with `hermes skills install official/web-development/react-best-practices` |
| Path | `optional-skills/web-development/react-best-practices` |
| Version | `0.1.0` |
| Author | Vercel (vercel-labs), Hermes Agent |
| License | MIT |
| Platforms | linux, macos, windows |
| Tags | `React`, `NextJS`, `Performance`, `Frontend` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# React Best Practices

Performance optimization guidance for React and Next.js applications, originating from Vercel Engineering's `react-best-practices` agent skill (vercel-labs/agent-skills, MIT). It contains 70 rules across 8 categories, prioritized by impact — from critical (eliminating waterfalls, bundle size) down to incremental micro-optimizations. Upstream rule IDs are preserved verbatim for traceability.

## When to Use

- Writing new React components or Next.js pages/routes
- Implementing data fetching (client- or server-side, RSC, API routes, server actions)
- Reviewing or refactoring React/Next.js code for performance
- Optimizing bundle size, load times, or interaction responsiveness

## Rule Categories by Priority

Work top-down: a fixed waterfall or a smaller bundle dwarfs any memoization win.

| Priority | Category | Impact | Rule prefix | Reference file |
|----------|----------|--------|-------------|----------------|
| 1 | Eliminating Waterfalls | CRITICAL | `async-` | `references/async-waterfalls.md` |
| 2 | Bundle Size Optimization | CRITICAL | `bundle-` | `references/bundle-optimization.md` |
| 3 | Server-Side Performance | HIGH | `server-` | `references/server-side.md` |
| 4 | Client-Side Data Fetching | MEDIUM-HIGH | `client-` | `references/client-data-fetching.md` |
| 5 | Re-render Optimization | MEDIUM | `rerender-` | `references/rerender-optimization.md` |
| 6 | Rendering Performance | MEDIUM | `rendering-` | `references/rendering-performance.md` |
| 7 | JavaScript Performance | LOW-MEDIUM | `js-` | `references/js-performance.md` |
| 8 | Advanced Patterns | LOW | `advanced-` | `references/advanced-patterns.md` |

Each reference file contains the full upstream rules for that category: rationale, incorrect vs. correct code, and impact estimates. Load only the categories relevant to the code at hand with `skill_view(name='react-best-practices', file_path='references/<file>.md')`.

## Core Rules at a Glance

The highest-leverage rules, by category (full detail in the reference files):

**Waterfalls (`references/async-waterfalls.md`)** — `async-parallel`: use `Promise.all()` for independent awaits. `async-defer-await`: move `await` into the branch that uses it. `async-cheap-condition-before-await`: check cheap sync conditions before awaiting. `async-api-routes`: start promises early, await late. `async-suspense-boundaries`: stream with Suspense instead of blocking the whole page. `async-dependencies`: use `better-all` for partially dependent tasks.

**Bundle (`references/bundle-optimization.md`)** — `bundle-barrel-imports`: import directly, never through barrel files. `bundle-dynamic-imports`: `next/dynamic` for heavy components. `bundle-defer-third-party`: load analytics after hydration. `bundle-conditional` and `bundle-preload`: load on activation, preload on hover/focus. `bundle-analyzable-paths`: keep import paths statically analyzable.

**Server (`references/server-side.md`)** — `server-cache-react`: use React `cache()` for per-request dedup. `server-cache-lru`: LRU cache across requests. `server-parallel-fetching` / `server-parallel-nested-fetching`: restructure components so fetches run concurrently. `server-hoist-static-io`: hoist static I/O to module level. `server-after-nonblocking`: use `after()` for non-blocking work. `server-auth-actions`: authenticate server actions like API routes. `server-serialization` / `server-dedup-props`: minimize and dedupe RSC prop payloads. `server-no-shared-module-state`: no module-level mutable request state.

**Client fetching (`references/client-data-fetching.md`)** — `client-swr-dedup`: SWR for automatic dedup. `client-event-listeners` / `client-passive-event-listeners`: dedupe global listeners, passive for scroll. `client-localstorage-schema`: version and minimize localStorage data.

**Re-renders (`references/rerender-optimization.md`)** — `rerender-derived-state-no-effect`: derive state during render, never via `useEffect` + `setState`. `rerender-functional-setstate`: functional updates for stable callbacks. `rerender-lazy-state-init`: pass a function to `useState` for expensive init. `rerender-no-inline-components`: never define components inside components. `rerender-memo`, `rerender-defer-reads`, `rerender-transitions`, `rerender-use-deferred-value`, `rerender-use-ref-transient-values`, and more.

**Rendering (`references/rendering-performance.md`)** — `rendering-content-visibility`, `rendering-hoist-jsx`, `rendering-conditional-render` (ternary, not `&&`), `rendering-hydration-no-flicker`, `rendering-resource-hints`, `rendering-usetransition-loading`, and more.

**JS micro-perf (`references/js-performance.md`)** — `js-set-map-lookups`, `js-index-maps`, `js-combine-iterations`, `js-early-exit`, `js-hoist-regexp`, `js-tosorted-immutable`, `js-request-idle-callback`, and more — for hot paths only.

**Advanced (`references/advanced-patterns.md`)** — `advanced-use-latest`, `advanced-event-handler-refs`, `advanced-init-once`, `advanced-effect-event-deps`.

## Applying the Rules with Hermes Tools

- Inspect code with `read_file` and locate suspect patterns with `search_files` — e.g. `search_files(pattern='await .*\n.*await', target='content', file_glob='*.tsx')` for sequential awaits, or `pattern='useEffect'` to audit effects for derived-state misuse.
- Apply fixes with `patch` (targeted find-and-replace), citing the rule ID in your explanation so reviewers can trace the rationale.
- Never use grep/cat/sed via terminal for this — `search_files`/`read_file`/`patch` are the correct tools.

## Pitfalls

- **Don't memoize first.** `useMemo`/`memo` on simple expressions adds overhead (`rerender-simple-expression-in-memo`); fix waterfalls and bundle size before re-render tuning.
- **`useEffect` for derived state is a bug, not a style choice** — it causes a double render and tearing (`rerender-derived-state-no-effect`).
- **Parallelizing dependent operations breaks correctness.** Only `Promise.all()` genuinely independent work; use `better-all` or restructuring for partial dependencies (`async-dependencies`).
- **Dynamic imports of tiny modules hurt** — the request overhead exceeds the saved bytes. Reserve `next/dynamic` for genuinely heavy components.
- **JS micro-optimizations (category 7) only matter in hot paths** — don't churn cold code for them during review.
- Rules assume modern React (18/19) and Next.js App Router; some (e.g. `rendering-activity`, `after()`) need recent versions — check the project's dependencies first.

## Applying During Code Review

1. Scan for sequential awaits, fetch-in-loop, and blocking data fetches → category 1 (CRITICAL).
2. Check imports: barrel files, heavy libs in initial bundle, eager third-party scripts → category 2 (CRITICAL).
3. Server code: missing `cache()`, serial component fetches, unauthenticated server actions, oversized RSC props → category 3.
4. Client fetching without SWR/dedup; unversioned localStorage → category 4.
5. Effects that set derived state, inline component definitions, unstable deps → category 5.
6. Only then consider rendering tweaks (6), JS micro-perf in hot paths (7), and advanced patterns (8).
7. For every finding, cite the upstream rule ID and load the matching `references/*.md` for the incorrect/correct example before proposing a `patch`.
