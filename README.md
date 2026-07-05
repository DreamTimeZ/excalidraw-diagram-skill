# Excalidraw Diagram Skill

A coding agent skill that generates beautiful and practical Excalidraw diagrams from natural language descriptions. Not just boxes-and-arrows - diagrams that **argue visually**.

Compatible with any coding agent that supports skills. For agents that read from `.claude/skills/` (like [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and [OpenCode](https://github.com/nicepkg/OpenCode)), just drop it in and go.

## What Makes This Different

- **Diagrams that argue, not display.** Every shape/group of shapes mirrors the concept it represents — fan-outs for one-to-many, timelines for sequences, convergence for aggregation. No uniform card grids.
- **Evidence artifacts.** As an example, technical diagrams include real code snippets and actual JSON payloads.
- **Built-in visual validation.** A Playwright-based render pipeline lets the agent see its own output, catch layout issues (overlapping text, misaligned arrows, unbalanced spacing), and fix them in a loop before delivering. Rendering is fully offline (React and Excalidraw are vendored as one bundle, the fonts are vendored, and the render browser blocks every network request), and a font that fails to load aborts the render instead of silently degrading it.
- **Brand-customizable.** All colors and brand styles live in a single file (`references/color-palette.md`). Swap it out and every diagram follows your palette.

## Installation

Clone or download this repo, then copy it into your project's `.claude/skills/` directory:

```bash
git clone --depth 1 https://github.com/coleam00/excalidraw-diagram-skill.git
cp -r excalidraw-diagram-skill .claude/skills/excalidraw-diagram
```

To copy the skill into another project later, copy a fresh clone, not an already-set-up copy: `cp -r` ignores `.gitignore` and drags the ~145 MB `references/.venv` along, whose scripts hardcode absolute paths. If you must copy in place, exclude it (`rsync -a --exclude .venv`); a plain `uv sync` in the destination rebuilds it either way.

## Setup

The skill includes a render pipeline that lets the agent visually validate its diagrams. There are two ways to set it up:

**Option A: Ask your coding agent (easiest)**

Just tell your agent: *"Set up the Excalidraw diagram skill renderer by following the instructions in SKILL.md."* It will run the commands for you.

**Option B: Manual**

```bash
cd .claude/skills/excalidraw-diagram/references
uv sync
uv run playwright install chromium
```

After setup, rendering needs no network: React 19.2.7 and Excalidraw 0.18.1 are vendored as a single esbuild bundle under `references/vendor/`, together with the Excalidraw web fonts (CJK excluded). The render browser blocks every real network request. Verify the pipeline at any time, or run the regression suite:

```bash
uv run python render_excalidraw.py --check   # render a built-in fixture end to end
uv run --group dev pytest                     # full render regression suite
```

To upgrade the vendored versions, edit the pins at the top of `references/build_vendor.sh` and re-run it (requires Node, pnpm and network). End users never need this: the built bundle is committed.

```bash
bash references/build_vendor.sh
```

## Usage

Ask your coding agent to create a diagram:

> "Create an Excalidraw diagram showing how the AG-UI protocol streams events from an AI agent to a frontend UI"

The skill handles the rest — concept mapping, layout, JSON generation, rendering, and visual validation.

## Customize Colors

Edit `references/color-palette.md` to match your brand. Everything else in the skill is universal design methodology.

## File Structure

```
excalidraw-diagram/
  SKILL.md                          # Design methodology + workflow
  .github/workflows/render.yml      # CI: runs the render regression suite
  references/
    color-palette.md                # Brand colors (edit this to customize)
    element-templates.md            # JSON templates for each element type
    json-schema.md                  # Excalidraw JSON format reference
    render_excalidraw.py            # Render .excalidraw / Obsidian .excalidraw.md to PNG (offline; --both for light+dark)
    render_template.html            # Browser template (loads the vendored bundle)
    build_vendor.sh                 # Rebuild vendor/ from pinned npm versions
    pyproject.toml                  # Python dependencies (pinned)
    uv.lock                         # Locked dependency versions
    vendor/                         # esbuild bundle (React 19 + Excalidraw 0.18), fonts, licenses
    tests/                          # Render regression suite
```
