---
name: Cockpit Yannis
description: Mobile-first PWA showing personal health trajectories versus long-term goals.
---

<!-- SEED: re-run /impeccable document once there's code to capture the actual tokens and components. -->

# Design System: Cockpit Yannis

## 1. Overview

**Creative North Star: "The Sage Cabinet"**

A medical-grade consultation room rendered as a personal mobile app. "Sage" carries triple duty: the color (vert sauge profond), the herb (calm, botanical, restorative), and the wisdom (long-term perspective over daily noise). "Cabinet" is both the well-organized doctor's office and the gallery cabinet, a place where things are kept with care and consulted with respect.

The system is **Committed** in color strategy: the deep sage carries 30 to 50 percent of any given surface, anchoring the user in a single, confident voice. The system is **Responsive** in motion: it reacts, it does not perform. Charts redraw with quiet exponential easing. State changes confirm with restraint. Nothing dances to be noticed.

This explicitly rejects: SaaS-cream gradients and tile grids, MyFitnessPal-style red alarms over kilocalorie deficits, Strava-style streak badges and confetti, Apple-Health centered-tile clichés, dashboard-SRE neon-on-black, and the AI Slop hero-metric template (big number, small label, supporting stats, gradient accent). If a glance at the screen lets someone say "made by AI", the screen has failed.

**Key Characteristics:**
- Surface dominated by deep sage; cream as relief, not the other way around.
- Trajectory charts (real curve, ideal path, projection band) are the default visualization.
- Numerical precision lives in tabular figures; prose copy stays short and clinical.
- Mobile-first, thumb-zone primary, single-screen-no-scroll for the hero.
- Calm at rest, responsive on touch, no surprise motion.

## 2. Colors

A committed palette that lives inside the sage register: a single hue carries the personality, with warm cream as breathing space and ambre brûlé reserved for trajectory deviation. No reds, no greens-fluo, no gradients.

### Primary

- **Deep Sage** (`oklch(50% 0.08 150)` ≈ `#5e7a64`): The dominant surface color. Covers chart backgrounds, hero panel, app shell header. Background under cream content. Cream text reads on it.

### Secondary

- **Sage Wash** (`oklch(68% 0.06 150)` ≈ `#8aa490`): Lighter sibling of Deep Sage. Used for active states, secondary fills, on-track indicators on trajectory bands. Never used as primary background.

### Tertiary (deviation signal only)

- **Ambre Brûlé** (`oklch(70% 0.13 75)` ≈ `#d4a460`): Trajectory deviation signal. Used when a metric leaves its tolerance band by more than the documented threshold. Never as decoration. Never red. The cockpit does not alarm; it tints.

### Neutral

- **Warm Cream** (`oklch(96% 0.005 150)` ≈ `#f5f3ee`): Primary content background, cards, chart paper. Slightly tinted toward sage hue (chroma 0.005) so it never reads as pure white.
- **Sage Ink** (`oklch(22% 0.02 150)` ≈ `#23302a`): Default body text on cream. Tinted toward sage hue so it never reads as pure black.
- **Sage Ash** (`oklch(55% 0.015 150)` ≈ `#7d847f`): Secondary text, labels, axis ticks on chart paper.
- **Sage Mist** (`oklch(88% 0.01 150)` ≈ `#d6d9d4`): Dividers, hairline borders, chart gridlines on cream.

### Named Rules

**The Committed Sage Rule.** Deep Sage is the surface, not an accent. Any screen that does not have at least 30 percent of its viewport in Deep Sage has lost its voice. A screen that is more than 60 percent Deep Sage has crushed its content. Stay in the band.

**The No-Alarm Rule.** Red is not in the palette. A trajectory drifting off path is communicated by the curve itself bending out of its tolerance band, optionally tinted with Ambre Brûlé. There is no badge that says "alert". There is no number that turns red. The user has lived through five years of red-quota dashboards; the cockpit refuses to be the sixth.

**The One Tint Per Tile Rule.** Each pillar (Corps, Métabolique, Os, Cardio, Récupération) may tint its drill-down screen with one variant of sage opacity, never with a foreign hue. Category recognition comes from typography, ordering, and shape, not from a rainbow.

## 3. Typography

**Display Font:** [GT America, Söhne, or Untitled Sans family; to be finalized at implementation]
**Body Font:** Same family, body weights, with tabular figures enabled (`font-feature-settings: "tnum"`).
**Label/Mono Font:** [None planned; tabular figures within the sans cover numerical alignment.]

**Character:** Warm humanist sans, with confident proportions and a low x-height feel. Not technical (no JetBrains, no Inter, no Geist). Not editorial-serif (no New York Times Health). The pairing should feel like a well-printed clinical journal that respects the reader.

### Hierarchy

- **Display** (medium 500, clamp 2.5–3.5rem, line-height 1): Hero figure on the main screen (the trajectory headline, e.g., the current weight or score). Tabular figures mandatory. One per screen.
- **Headline** (medium 500, 1.5–1.75rem, line-height 1.15): Pillar titles in drill-down screens.
- **Title** (medium 500, 1.125rem, line-height 1.3): Tile titles on the cockpit grid.
- **Body** (regular 400, 1rem, line-height 1.5): Body copy in drill-down. Max line length 65–75ch on tablet and up; full container width on phone.
- **Label** (medium 500, 0.75rem, letter-spacing 0.04em, all caps reserved for true labels only): Axis ticks, category tags, status chips.
- **Numeric** (medium 500, tabular figures, size inherits from context): Used wherever a number must align across rows. Bold weight banned for numbers; emphasis through size or color only.

### Named Rules

**The Tabular Figure Rule.** Every numerical value in the cockpit uses tabular figures. No exception. Misaligned digits in a health dashboard are visual lying.

**The No-Number-Bold Rule.** Numbers carry their own weight through value and context. Bolding a number is shouting. If a value needs emphasis, increase its size or color contrast, never its weight.

## 4. Elevation

Mostly flat by default, with restrained tonal layering between Deep Sage (lower) and Warm Cream (upper). Shadows are not part of the visual language: the cream content surfaces sit on the sage shell through pure color contrast, not through drop shadows.

### Shadow Vocabulary

- **Hover lift** (`box-shadow: 0 1px 2px oklch(22% 0.02 150 / 0.06)`): Reserved for primary interactive tiles on tap-down (mobile) or hover (desktop). Subliminal, never decorative.
- **No ambient shadows.** Cards do not float. The shell does the structural work through color and spacing.

### Named Rules

**The Flat-By-Default Rule.** No element has a shadow at rest. Shadows are a response to user input (focus, tap-down), not a decoration applied at render time. If a surface needs to feel "lifted" without interaction, it is wrong; reach for spacing, color, or border instead.

## 5. Components

[Omitted in seed mode. No components exist yet. Will be populated on next `/impeccable document` after first screen is built.]

## 6. Do's and Don'ts

### Do:

- **Do** make Deep Sage carry 30 to 50 percent of every screen. The Committed strategy is the voice.
- **Do** show every numeric metric with three layers: actual curve, ideal path, projection band. The trajectory IS the metric.
- **Do** use tabular figures (`font-feature-settings: "tnum"`) for every number, everywhere.
- **Do** use Ambre Brûlé sparingly to tint a curve that leaves its tolerance band. Never as fill, never as decoration.
- **Do** keep all interactive zones ≥ 44px on mobile, in the thumb zone (lower 60% of the viewport).
- **Do** respect `prefers-reduced-motion`: chart redraws become instant; state transitions become opacity swaps with no easing.
- **Do** tint neutrals toward the sage hue (chroma 0.005–0.01). Never use pure `#fff` or `#000`.

### Don't:

- **Don't** use red for any metric, alert, or value. The No-Alarm Rule is absolute. The user has explicit binge-restriction history; red kilocalorie counters are a documented trigger.
- **Don't** add streak badges, celebration confetti, level-up animations, or any gamification primitive. PRODUCT.md anti-reference: Strava, Duolingo, Whoop.
- **Don't** use the hero-metric template (big number, small label, supporting stats, gradient accent). PRODUCT.md anti-reference: AI Slop. If a designer would recognize this layout from a SaaS pitch deck, it is wrong.
- **Don't** use gradient text or gradient buttons. Use solid sage, solid cream, or one of the two text colors.
- **Don't** use glassmorphism, backdrop blurs, or translucent cards. Cream sits on sage through color contrast, not through frosted layers.
- **Don't** nest cards in cards. A tile is a tile; its contents are direct children, not sub-cards.
- **Don't** use side-stripe borders (`border-left` colored accent) on rows, tiles, or alerts. Use full borders, background tints, or nothing.
- **Don't** bold numerical values. PRODUCT.md anti-reference: MyFitnessPal-style chiffres anxiogènes.
- **Don't** auto-rotate the carousel, auto-play motion on load, or animate elements into view on scroll. PRODUCT.md design principle: Calme = confiance.
- **Don't** open a modal as the first thought. Modals are usually laziness. Exhaust inline and progressive alternatives first; drill-down screens replace most modals here.
- **Don't** use Inter as the body font. PRODUCT.md anti-reference: SaaS-cream cliché. The typography direction is warm humanist sans (GT America, Söhne, Untitled Sans), not the default Vercel/Linear stack.
- **Don't** use em dashes in copy. Use commas, colons, semicolons, periods, or parentheses. Also not `--`.
