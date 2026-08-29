# Engineered Prompts for `plugin87/ux-ui-agent-skills`

This kit turns Claude into a "Senior Design Architect" via a `CLAUDE.md` persona file, DTCG design tokens, 50 component specs, WCAG 2.2 accessibility references, an any-framework code adapter protocol, and 17 runnable `/skills`. It's built for **Claude Code** or any Claude-powered IDE — `CLAUDE.md` auto-loads in a project and self-routes plain requests to the right knowledge layer.

**How to use these prompts:**
- **In Claude Code / an IDE:** install the kit first (`npx ux-ui-agent-skills init` or clone it into your project), open the project, then paste any prompt below as-is — including the `/skill-name` prefix where shown.
- **In claude.ai (web/desktop) without Claude Code:** add the relevant files as Project knowledge (at minimum `CLAUDE.md`, `taste/design-taste.md`, `tokens/*.json`, and `accessibility/wcag-checklist.md` — add more per task), then use the prompts below *without* the `/command` prefix, since slash commands only resolve inside Claude Code.

Each prompt below is written to be specific about inputs, constraints, and deliverable format — swap out anything in `[brackets]`.

---

## 1. Session Kickoff

Use once per project to lock in the persona and constraints before anything else.

```
Act as a senior design architect with 15+ years in design systems, accessibility, and production front-end engineering. For this session:
- Design token architecture: 3-tier (primitive → semantic → component), DTCG format
- Accessibility floor: WCAG 2.2 AA, flag anything that could reach AAA
- Target framework: [React + Tailwind / Next.js / SwiftUI / Vue / etc.]
- Brand reference or aesthetic direction: [brand name, mood words, or "none yet — help me define one"]
- Always state which tokens, components, or a11y criteria you're applying, and never hardcode colors, spacing, or timing values that should be tokens.

Confirm you're set up, then wait for my first request.
```

---

## 2. Design Tokens — New Brand Palette

```
/design-tokens

Generate a complete DTCG-format color token set for a [industry, e.g. fintech] brand.
- Primary brand color: [hex or description, e.g. "a trustworthy blue"]
- Secondary/accent: [hex, or "your recommendation"]
- Include: primitive scale (50–900) for each hue, semantic aliases (action.primary, text.secondary, surface.card, border.default, feedback.error/warning/success), and component-level tokens for button and input.
- Provide both light and dark theme values for every semantic token.
- Verify every text/UI-component pair hits WCAG 2.2 AA contrast (4.5:1 text, 3:1 UI) in both themes and show the ratios in a table.
```

## 3. Design Tokens — Extend Existing System

```
/design-tokens

Here is our current token set: [paste tokens/colors.json or attach file].
Extend it to support:
- A second brand theme for [sub-brand/white-label name] without duplicating primitives
- A "compact" density mode (spacing + sizing tokens only)
Keep every new token DTCG-compliant and aliasable back to the existing primitives. List exactly which files/keys change.
```

---

## 4. Component Design — Full Spec

```
/design-component

Spec a [component name, e.g. "Date Picker"] using Atomic Design conventions.
Include:
- Anatomy (labeled parts)
- All variants (e.g. size, emphasis)
- All 8 interaction states (default, hover, focus, active, disabled, loading, error, selected)
- Token mapping for every visual property (no raw values)
- Full a11y spec: ARIA role/pattern, keyboard interactions, focus management, screen-reader announcements
- Responsive behavior at 320px, 768px, 1024px+
```

## 5. Component Design — Accessibility-First Redesign of One Component

```
/design-component

Here's our current [component name] implementation: [paste code or describe it].
Redesign the spec so it passes WCAG 2.2 AA, specifically:
- Touch target size (24×24px minimum, WCAG 2.5.8)
- Focus indicator visibility (Focus Not Obscured)
- Keyboard operability without a mouse
Keep the visual language recognizable — don't restyle from scratch, just fix what's broken. List each change against the specific success criterion it satisfies.
```

---

## 6. Code Generation — Component in a Target Framework

```
/design-code

Generate production-ready code for the [component name] spec above, targeting [React + Tailwind / SwiftUI / Vue / Flutter / etc.].
Requirements:
- Consume the theme via CSS custom properties / design tokens — zero hardcoded hex, px, or ms values
- Cover all states from the spec, including dark mode
- Include ARIA attributes and keyboard handlers, not just visual states
- If the framework has an idiomatic pattern for this (e.g. `forwardRef` + `cva` in React, `ButtonStyle` in SwiftUI), use it
```

## 7. Code Generation — Full Screen

```
/design-code

Build a [screen name, e.g. "checkout page"] in [framework] using our existing token theme and component library.
Layout requirements:
- [list sections/content blocks]
- Responsive from 320px to desktop, no horizontal overflow at any breakpoint
- Uses only components already specified — flag anything new that needs its own spec first instead of inventing ad hoc markup
```

---

## 8. Design Review

```
/design-review

Review this [screen/flow name]: [paste code, describe it, or attach a screenshot].
Score it across the 6 weighted dimensions (Visual Hierarchy 20%, Consistency 20%, Accessibility 20%, Usability 20%, Responsiveness 10%, Performance 10%) and evaluate against Nielsen's 10 heuristics.
Output a findings table with columns: Finding | Severity (Critical/Major/Minor/Enhancement) | Heuristic or Dimension | Recommended Fix.
```

---

## 9. Accessibility Audit

```
/a11y-audit

Audit this [form/flow/page]: [paste code or describe it] against WCAG 2.2 AA.
- Check contrast (text 4.5:1, UI components 3:1), keyboard navigation, focus order, ARIA correctness, and touch target size
- Prioritize findings P0 (blocks a user) / P1 (significant barrier) / P2 (best practice)
- For each P0/P1, give the specific success criterion number and a concrete fix
```

---

## 10. Apply an Aesthetic / Design System Direction

```
/apply-aesthetic [design system name, e.g. "linear" / "stripe" / "notion"]

Apply this aesthetic direction to [our dashboard / landing page / component library]. Re-point our existing semantic tokens toward the new visual language (spacing rhythm, type scale, motion feel, elevation approach) without inventing a parallel token system.
After applying it, re-verify contrast in light and dark mode and flag anything that regressed.
```

## 11. Migrate Between Design Systems

```
/migrate-design-system

Map our current tokens/components from [Material 3 / Apple HIG / Fluent / shadcn / Radix] to our internal token system (or vice versa).
Produce a role-based crosswalk table (their token/component → our equivalent) and flag any concepts that don't have a direct equivalent, with a recommended substitution.
```

---

## 12. Redesign an Existing UI (Audit-First)

```
/redesign

Here is our current [page/flow]: [paste code or attach screenshots].
Before changing anything, run a design review + accessibility audit and summarize what's actually broken vs. what's just dated.
Then propose an upgraded version that fixes the P0/P1 issues and modernizes the visual language, without changing the information architecture or breaking existing user flows. Call out every breaking change explicitly.
```

---

## 13. Prototyping & Usability Testing

```
/prototype

I need to validate [feature/flow] before building it. Walk me up the fidelity ladder from concept to testable prototype:
1. Sketch the core flow as a written user journey (steps, decisions, drop-off risks)
2. Define what "low," "mid," and "high" fidelity should look like for this specific feature
3. Write a 5-participant usability test script with 3-5 tasks and success criteria
```

---

## 14. UX Writing / Microcopy

```
/ux-writing

Write the microcopy for [flow name, e.g. "password reset"]:
- Button labels (all states)
- Error messages for [list likely failure cases]
- Empty state copy
- Success confirmation
Match a [tone descriptor, e.g. "calm and direct, no exclamation points"] voice. Follow inclusive-language guidelines and keep every error message actionable (tell the user what to do next, not just what went wrong).
```

---

## 15. Bonus — Brand Kit from a Brief

```
/brandkit

Generate a complete token foundation from this brief: [1-2 sentence brand description, target audience, and any existing brand colors/fonts].
Deliver: primitive → semantic → component token layers (color, type, spacing, motion), light + dark themes, and a theme.css. Verify every pair against WCAG 2.2 AA before presenting it.
```

## 16. Bonus — Screenshot/Reference Image to Code

```
/image-to-code

[Attach a screenshot or reference image.]
Infer the underlying design system (spacing scale, type scale, color roles, component patterns) from this image, then generate token-driven [framework] code that reproduces it — don't hardcode values pulled directly from the pixels, map them to tokens first.
```

---

## Typical Chained Flow

For a full feature build, run these in sequence — each skill hands context to the next:

```
1. /apply-aesthetic [reference brand]      → set visual direction
2. /design-component [component name]      → spec with states + a11y
3. /design-code [component] in [framework] → production code
4. /a11y-audit                             → verify contrast, keyboard, focus
5. /design-review                          → score + findings before ship
```
