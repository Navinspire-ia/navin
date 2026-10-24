---
name: frontend-design
description: Gives a new or reworked interface a distinctive, deliberate visual identity instead of a templated look - subject-driven aesthetic direction, typography, palette, layout, restrained motion and interface copy, planned as a token system and critiqued before code. Use when the user asks to "design a landing page", "build a dashboard that does not look generic", "make this UI look less templated", "pick a visual direction", "choose fonts and colors for this app", "redesign this page", or any frontend work where the look is part of the brief.
metadata: {"navin":{"category":"development"}}
---

# Frontend Design

Work like the art director of a small studio whose reputation rests on never handing two clients the same look. Assume the client already turned down proposals that felt stock or interchangeable, and is paying for a point of view: commit to choices about palette, type and layout that belong to this brief and no other, and take a visual risk when the brief can carry it.

This skill decides the direction. It works together with the other Navin UI playbooks:

- `ui-ux-pro-max` supplies the design-system search (styles, palettes, font pairings, UX rules). Use its results as raw material, then make the choice yourself.
- The design system stays locked (Google MUI, Microsoft Fluent or IBM Carbon). The direction lives in the theme: palette, typography, shape, spacing and component overrides mapped onto the ThemeProvider, never page-local CSS or a homemade kit.
- `make-interfaces-feel-better` handles the final polish pass once the page works.

## Start from the subject

When the brief does not say what the product is, work it out before drawing anything and check it with the user: propose one concrete subject, who the audience is, and the single main job of the page. Use what you already know about the user or their project as a hint. Distinctive choices come from the subject's own world - its industry, materials, vocabulary and habits. A game for children aged 8 to 11 and a risk console for bond traders should not share a single visual decision. Use the brief's real content and wording throughout, not filler.

## Principles

**The opening screen.** On the web, the hero is the first impression. Lead with the most characteristic thing in the subject's world, in the form that suits it best: a headline, a photograph, an animation, a live demo, an interactive moment, or something else. Choose on purpose. A giant number with a small caption, a row of supporting stats and a gradient accent is the reflex treatment; only use it when it truly is the best answer.

**Typography is the voice.** Display and body text do not need different families: one family, or two at most, and if two, make the contrast obvious. Pick typefaces for this brief rather than the families you would reach for on any project. Set a real type scale in the spirit of classic book typography, with intentional weights, widths and spacing. When type is used as a headline or a graphic element, treat the letterforms as part of the composition, not as a neutral container for words.

**Measure and leading.** Keep lines under about 80 characters. Serif text can run slightly longer; give serif body copy a little more line height than sans-serif.

**Typographic tells to avoid.** These are the most common signatures of a generated page:

- highlighting one word or phrase of a headline with italics, bold or a different color;
- all-caps labels;
- decorative labels stacked above content that do not add information.

**Structure carries meaning.** Outlines, borders, numbering, eyebrows, dividers and labels exist to encode something about the content, not to decorate it. Numbered markers such as 01 / 02 / 03 are only right when the content really is a sequence, like steps or a timeline. Check before adding them.

**Motion with intent.** Use motion that nobody triggered sparingly, and only to direct attention. One orchestrated moment, such as a single load sequence or one reveal, lands better than effects sprinkled everywhere. A fade-and-rise entrance on every section and a hover lift on every card are the default and read as machine-made. Motion that answers an action (opening, expanding, confirming) is welcome when it shows what changed. Implement it with `framer-motion` and respect reduced-motion settings.

**Words are part of the design.** Briefs often arrive without real copy, and inventing it is part of the job. Generic copy makes a page feel as templated as generic visuals. See "Writing inside the design" below.

## Process: plan, check against the brief, build, critique

For calibration, generated interfaces currently gravitate to a few looks:

1. a warm cream page (near `#F4F1EA`) with a high-contrast serif display and a terracotta or clay accent (near `#D97757`, the house accent of a well-known assistant, so on someone else's brief it gives the game away);
2. a near-black page with one acid-green or vermilion accent;
3. a broadsheet layout: hairline rules, square corners, dense newspaper columns;
4. the SaaS card kit: content cut into identical rounded cards, one radius on everything whatever the hierarchy, the same soft grey shadow (`rgba(0,0,0,.1)`) under each, and gradient washes as decoration;
5. template chrome that shows up whatever the subject: a letter-spaced ALL-CAPS eyebrow over every heading, metadata joined with middle dots (`A · B · C`), labels shaped as `WORD` plus a spaced long dash plus a fragment, tinted near-black (`#0B0B0B`, `#111`) used instead of black, a monospace face for small data labels, and a `→` tacked onto link and button text.

Each of these suits some brief, but they are defaults, not decisions, and they appear regardless of subject. When the brief fixes a visual direction, follow it exactly: the brief's own words always win, even when it asks for one of these looks. When it leaves an axis open, do not spend that freedom on a default. Like a hired designer, balance what you do well with treating each project as a chance to try something new.

### Pass 1: a compact design plan

Before any code, write a short plan from the brief, expressed as tokens you will map onto the locked design system's theme:

- **Color**: the base palette as 4 to 6 named hex values (for example `ink #1F2A44`, `paper #F7F9FC`, `signal #E4572E`), with their roles (background, surface, text, accent, state).
- **Type**: the typefaces and their roles, plus the scale (sizes, weights, line heights).
- **Layout**: the layout idea in one-sentence descriptions and small ASCII wireframes to compare options. Say how content aligns: left, centered or justified.
- **Principles**: two or three sentences on what makes this page unlike its neighbours.

Example wireframe for comparing two hero options:

```
A: split                         B: full-bleed type
+---------------+-----------+    +---------------------------+
| Headline      |  product  |    |  HEADLINE SET HUGE        |
| one line sub  |  in use   |    |  across the whole width   |
| [Start trial] |  (video)  |    |  [Start trial]  [Watch]   |
+---------------+-----------+    +---------------------------+
```

### Pass 2: check the plan against the brief

Review the plan before building. For each part, ask whether you would have produced the same thing for any similar brief (mentally run a neighbouring prompt and see if you land in the same place). If a part is the generic answer rather than a choice made for this subject, revise it and say what you changed and why. Write code only once the plan holds up, and follow the revised version.

### Pass 3: build

- Map the tokens onto the theme first (palette, typography, shape, spacing, component overrides), then compose pages from the shared kit.
- Watch selector specificity in any styles you add. Classes that cancel each other out are easy to create, typically a type selector such as `.section` fighting an element class such as `.cta` over padding or margin between sections. Prefer theme overrides and component props to ad hoc selectors.
- Use the real content of the brief; no lorem, no dead buttons.

### Pass 4: restraint and self-critique

Spend the boldness in one place. Let a single element be the memorable one, keep everything around it calm and disciplined, and remove any decoration the brief does not need. Reach a quality floor without announcing it: works down to mobile, visible keyboard focus, reduced motion respected, readable contrast, a harmonious palette. Critique while you build and look at the result: open the Preview, take a screenshot when the environment allows it, and adjust. A screenshot tells you more than a thousand tokens of reasoning. Before calling it done, do what the old advice says about getting dressed: look in the mirror and take one accessory off.

Keep a short note of the directions you tried (the project's continuity notes are a good place) so the next pass on this project can try something new instead of repeating itself.

## Writing inside the design

Text appears in an interface for one reason: to make it easier to understand and use. It is design material, not ornament. Bring the same restraint to copy that you bring to spacing and color. Before writing, ask what the screen needs to say and how to say it so the person can find their way.

- **The user's vocabulary.** Name things the way users think about them, not the way the system is built. People manage notifications, not webhook configuration. Say what something is or does in plain words instead of selling it. Clear beats clever, especially for newcomers.
- **Active voice.** A button says exactly what happens: "Save changes", not "Submit". An action keeps its name across the whole flow, so a "Publish" button leads to a "Published" confirmation. Consistent vocabulary is how people learn to navigate a product.
- **Errors and empty states give direction.** Say what went wrong and how to fix it, in the interface's voice rather than a person's. Errors do not apologise and are never vague about what happened. An empty screen invites the next action.
- **Tone.** Plain verbs, sentence case, no filler, tuned to the brand and the audience. Each piece of text does exactly one job.

## Example briefs

```
"Create a dashboard for a music streaming app"
"Build a landing page for an AI security startup"
"Design a settings panel with dark mode"
```

For each, start from the subject (listening habits and album art; threat timelines and audit trails; the specific settings people actually change), write the four-part plan, check it against the defaults above, then build it inside the locked design system.
