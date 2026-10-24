# Frontend Design

Distinctive, production-grade interfaces that do not look like every other generated page.

## What it does

Navin uses the `frontend-design` skill automatically for frontend work where the look matters. It produces production code with:

- a clear aesthetic direction drawn from the subject of the brief;
- deliberate typography and a named color palette;
- one memorable visual moment instead of scattered effects;
- restrained, meaningful motion (`framer-motion`);
- interface copy written from the user's point of view.

The direction is expressed as a small token system (color, type, layout, principles), checked against the usual generated defaults, then mapped onto the theme of the locked design system (Google MUI, Microsoft Fluent or IBM Carbon). It never replaces the design system with a homemade kit.

## How it works

1. **Subject first**: when the brief does not say what the product is, Navin proposes a subject, an audience and the page's main job, and checks them with you.
2. **Plan**: 4 to 6 named hex colors, typefaces and their roles, a layout idea with small ASCII wireframes, and two or three design principles.
3. **Check**: every part of the plan that would fit any similar brief is revised, and Navin says what changed and why.
4. **Build**: tokens go into the theme, pages are composed from the shared kit, with real content.
5. **Critique**: Navin looks at the result in Preview, removes one decoration too many, and checks mobile, keyboard focus, reduced motion and contrast.

## Examples

```
"Create a dashboard for a music streaming app"
"Build a landing page for an AI security startup"
"Design a settings panel with dark mode"
```

Sample plan produced before any code:

```markdown
Color: ink #1F2A44 (text), paper #F7F9FC (background), slate #5B6B8C (secondary),
       signal #E4572E (one accent, alerts only), moss #3C7A5A (success)
Type: Space Grotesk for headings, Source Serif 4 for long reading, scale 14/16/20/28/44
Layout: left-aligned, one wide timeline column with a narrow evidence rail on the right
Principles: the live threat timeline is the hero; everything else stays quiet
```

## Works with

- `ui-ux-pro-max`: design-system search for styles, palettes and font pairings;
- `make-interfaces-feel-better`: the final polish pass.

## Version

1.0.0
