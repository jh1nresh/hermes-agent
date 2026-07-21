---
title: "Theme Factory — Professional font and color themes for HTML artifacts"
sidebar_label: "Theme Factory"
description: "Professional font and color themes for HTML artifacts"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Theme Factory

Professional font and color themes for HTML artifacts.

## Skill metadata

| | |
|---|---|
| Source | Optional — install with `hermes skills install official/creative/theme-factory` |
| Path | `optional-skills/creative/theme-factory` |
| Version | `0.1.0` |
| Author | Anthropic (anthropics), Hermes Agent |
| License | Apache-2.0 |
| Platforms | linux, macos, windows |
| Tags | `Themes`, `Design`, `HTML`, `Styling` |
| Related skills | [`claude-design`](/docs/user-guide/skills/bundled/creative/creative-claude-design), [`sketch`](/docs/user-guide/skills/bundled/creative/creative-sketch) |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Hermes loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Theme Factory

Ported from Anthropic's Agent Skills repo (github.com/anthropics/skills, `skills/theme-factory`, Apache-2.0 — full license text in `references/LICENSE.txt`). A curated collection of 10 professional themes — cohesive color palettes plus header/body font pairings — for styling any HTML/web artifact Hermes generates: slide decks, docs, reports, landing pages, dashboards. Also supports generating brand-new themes on the fly when none of the presets fit.

## When to Use

- The user asks for a slide deck, report, landing page, or other HTML artifact and wants it to look polished/professional rather than default-browser plain.
- An existing HTML artifact needs a consistent visual identity (colors + fonts) applied throughout.
- The user asks for "a theme", names one of the gallery themes below, or describes a vibe ("something warm and autumnal") that maps to one.
- For full bespoke page design (layout, components, interactions), prefer the `claude-design` skill; for quick throwaway mockup variants, `sketch`; for infra diagrams, `architecture-diagram`. This skill is specifically the **styling layer**: pick/apply a font+palette theme.

## Theme Gallery

| # | Theme | Vibe | Fonts (header / body) | Palette |
|---|-------|------|-----------------------|---------|
| 1 | Ocean Depths | Professional, calming maritime | DejaVu Sans Bold / DejaVu Sans | `#1a2332` `#2d8b8b` `#a8dadc` `#f1faee` |
| 2 | Sunset Boulevard | Warm, vibrant sunset energy | DejaVu Serif Bold / DejaVu Sans | `#e76f51` `#f4a261` `#e9c46a` `#264653` |
| 3 | Forest Canopy | Natural, grounded earth tones | FreeSerif Bold / FreeSans | `#2d4a2b` `#7d8471` `#a4ac86` `#faf9f6` |
| 4 | Modern Minimalist | Clean contemporary grayscale | DejaVu Sans Bold / DejaVu Sans | `#36454f` `#708090` `#d3d3d3` `#ffffff` |
| 5 | Golden Hour | Rich, warm autumnal | FreeSans Bold / FreeSans | `#f4a900` `#c1666b` `#d4b896` `#4a403a` |
| 6 | Arctic Frost | Cool, crisp winter clarity | DejaVu Sans Bold / DejaVu Sans | `#d4e4f7` `#4a6fa5` `#c0c0c0` `#fafafa` |
| 7 | Desert Rose | Soft, sophisticated dusty tones | FreeSans Bold / FreeSans | `#d4a5a5` `#b87d6d` `#e8d5c4` `#5d2e46` |
| 8 | Tech Innovation | Bold, high-contrast modern tech | DejaVu Sans Bold / DejaVu Sans | `#0066ff` `#00ffff` `#1e1e1e` `#ffffff` |
| 9 | Botanical Garden | Fresh, organic garden colors | DejaVu Serif Bold / DejaVu Sans | `#4a7c59` `#f9a620` `#b7472a` `#f5f3ed` |
| 10 | Midnight Galaxy | Dramatic, cosmic deep tones | FreeSans Bold / FreeSans | `#2b1e3e` `#4a4e8f` `#a490c2` `#e6e6fa` |

Full per-theme specs (color role assignments, best-used-for guidance) live in `references/themes/<theme-name>.md` — one file per theme, e.g. `references/themes/ocean-depths.md`. Read the theme's file before applying it; the role notes (which hex is background vs. accent vs. text) matter for contrast.

## Procedure

1. **Offer choices.** Show the user the gallery table above (or the shortlist matching their described vibe) and ask which theme to apply. Wait for explicit confirmation before styling.
2. **Read the spec.** `read_file` the chosen theme's `references/themes/<name>.md` to get each color's role (background / accent / highlight / text) and the exact font pairing.
3. **Generate the artifact.** Use `write_file` to create the HTML. Encode the theme as CSS custom properties so it applies consistently:
   ```css
   :root {
     --bg: #1a2332; --accent: #2d8b8b;
     --accent-2: #a8dadc; --text: #f1faee;
     --font-head: "DejaVu Sans", "Helvetica Neue", Arial, sans-serif;
     --font-body: "DejaVu Sans", "Helvetica Neue", Arial, sans-serif;
   }
   ```
   Apply roles as specified in the theme file — don't shuffle background and text colors arbitrarily.
4. **Restyle existing artifacts** by `read_file`-ing the current HTML, then `write_file`-ing it back with the theme's `:root` variables and font stacks swapped in (never sed/cat).
5. **Preview.** `browser_navigate` to `file:///absolute/path/to/artifact.html`, then `browser_vision` to visually verify colors render as intended, headings use the header font, and contrast is readable.

## Generating New Themes

When none of the 10 presets fit, create a custom theme in the same format:

1. From the user's description ("earthy but modern", brand colors, industry), pick 4 cohesive colors (dark anchor, primary accent, secondary/highlight, light neutral) and a header/body font pairing.
2. Give it an evocative two-word name in the gallery's style (e.g. "Copper Foundry").
3. Present the palette + fonts to the user for review **before** applying — optionally as a small HTML swatch card via `write_file` + browser preview.
4. Once approved, apply via the Procedure above. If it will be reused, save it as `references/themes/<new-name>.md` mirroring the existing files' structure.

## Pitfalls

- **Contrast**: light themes (Arctic Frost, Desert Rose) put light hexes as backgrounds and the darkest hex as text; dark themes (Ocean Depths, Midnight Galaxy, Tech Innovation) invert that. Read the role annotations — swapping roles produces unreadable output.
- **Fonts**: DejaVu/FreeSans/FreeSerif ship on most Linux systems but not macOS/Windows — always include fallback stacks (`Arial`, `Helvetica Neue`, `Georgia`, `sans-serif`/`serif`) so artifacts degrade gracefully.
- **Neon accents** (Tech Innovation's `#00ffff`) are for highlights/borders only — never body text on white.
- Don't apply a theme unprompted; the upstream skill's flow is show → ask → confirm → apply.
- Skipping the browser preview: hex codes that look fine in a table can clash on-screen; verify visually.

## Verification

- Open the artifact via `browser_navigate` (`file://` URL) and `browser_vision` to confirm: theme colors present, header vs. body font distinction visible, text readable against its background.
- Grep the generated HTML for the theme's hex codes to confirm all four palette colors were used.
- If the user reports a color looks off, re-read the theme spec and check role assignments before tweaking hexes.
