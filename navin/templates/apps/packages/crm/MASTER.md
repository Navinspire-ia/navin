# Navin CRM - design system

Pattern: Enterprise Gateway (app, pas landing).
Style: Flat, light + dark.
Motion: 6/10. Density: 5/10.

## Colors

| Token | Light | Dark |
| --- | --- | --- |
| primary | #0F172A | #E2E8F0 |
| on-primary | #FFFFFF | #0F172A |
| accent / CTA | #0369A1 | #38BDF8 |
| background | #F8FAFC | #020617 |
| foreground | #020617 | #F8FAFC |
| muted | #E8ECF1 | #1E293B |
| border | #E2E8F0 | #1E293B |
| destructive | #DC2626 | #F87171 |

Navy + CTA bleu. Pas de violet-sur-blanc. Pas de cream/terracotta.

## Type

Plus Jakarta Sans 400/500/600/700. Pas Inter, pas Roboto, pas Arial.

Headings: `text-wrap: balance`. Body: `text-wrap: pretty`.
Nombres dynamiques: `tabular-nums`.

## Motion

framer-motion, spring `{ type: "spring", duration: 0.3, bounce: 0 }`.
Stagger dashboard ~40ms. Respecter `prefers-reduced-motion`.

## Surfaces

Radius 0.625rem. Ombres legeres uniquement (pas de 3D). Hover 150-200ms.
