# Curated fonts for canvas work

Every family below is published under the SIL Open Font License 1.1 (OFL): free to use, embed, bundle and modify, including in commercial work. Two conditions: do not sell the font files on their own, and keep the license text when redistributing the fonts themselves. Rendering them into a PNG or PDF has no condition at all. Reserved Font Names apply to modified builds only.

Files are fetched from the Google Fonts repository on GitHub. URL pattern:

```
https://raw.githubusercontent.com/google/fonts/main/ofl/<family-folder>/<File>.ttf
```

`<family-folder>` is the family name lowercased without spaces (`crimsonpro`, `ibmplexmono`). Variable fonts carry their axes in square brackets in the file name (`Inter[opsz,wght].ttf`); percent-encode the brackets as `%5B` and `%5D` in the URL. `scripts/fetch_fonts.py --list` prints the exact URL of every file and `scripts/fetch_fonts.py "<Family>"` downloads it into `~/.cache/navin/fonts`.

## Manifest

| Family | Folder | File(s) | Role | Pairs with |
|---|---|---|---|---|
| Inter | inter | Inter[opsz,wght].ttf | UI and body sans (Navin house face) | IBM Plex Mono |
| IBM Plex Sans | ibmplexsans | IBMPlexSans[wdth,wght].ttf | Body sans, fallback for Inter | IBM Plex Mono |
| IBM Plex Mono | ibmplexmono | IBMPlexMono-Regular.ttf, IBMPlexMono-Bold.ttf | Mono for marks, indices and code | Inter |
| IBM Plex Serif | ibmplexserif | IBMPlexSerif-Regular.ttf | Editorial serif with a technical edge | IBM Plex Sans |
| JetBrains Mono | jetbrainsmono | JetBrainsMono[wght].ttf | Mono, brutalist labels | Bricolage Grotesque |
| Lora | lora | Lora[wght].ttf, Lora-Italic[wght].ttf | Warm editorial serif, long titles | Inter |
| Crimson Pro | crimsonpro | CrimsonPro[wght].ttf, CrimsonPro-Italic[wght].ttf | Book serif, italic display | Inter |
| Source Serif 4 | sourceserif4 | SourceSerif4[opsz,wght].ttf | Readable serif with optical sizes | Inter |
| Libre Baskerville | librebaskerville | LibreBaskerville[wght].ttf | Classic transitional serif, museum captions | Work Sans |
| Instrument Serif | instrumentserif | InstrumentSerif-Regular.ttf, InstrumentSerif-Italic.ttf | Condensed display serif, quiet scientific posters | IBM Plex Mono |
| Instrument Sans | instrumentsans | InstrumentSans[wdth,wght].ttf | Neutral sans with width axis | Instrument Serif |
| Fraunces | fraunces | Fraunces[SOFT,WONK,opsz,wght].ttf | Soft display serif, expressive covers | Manrope |
| DM Serif Display | dmserifdisplay | DMSerifDisplay-Regular.ttf | High-contrast display serif, one-word titles | DM Mono |
| Young Serif | youngserif | YoungSerif-Regular.ttf | Sturdy display serif | Work Sans |
| Gloock | gloock | Gloock-Regular.ttf | Heavy didone display | Inter |
| Work Sans | worksans | WorkSans[wght].ttf | Light grotesque for labels | Young Serif |
| Manrope | manrope | Manrope[wght].ttf | Geometric sans, modern covers | Fraunces |
| Outfit | outfit | Outfit[wght].ttf | Rounded geometric display sans | DM Mono |
| Space Grotesk | spacegrotesk | SpaceGrotesk[wght].ttf | Quirky grotesque, tech posters | Space Mono |
| Space Mono | spacemono | SpaceMono-Regular.ttf | Mono with character | Space Grotesk |
| DM Mono | dmmono | DMMono-Regular.ttf | Light mono for small marks | DM Serif Display |
| Red Hat Mono | redhatmono | RedHatMono[wght].ttf | Clean mono, technical diagrams | Tektur |
| Bricolage Grotesque | bricolagegrotesque | BricolageGrotesque[opsz,wdth,wght].ttf | Brutalist display grotesque | JetBrains Mono |
| Big Shoulders Display | bigshouldersdisplay | BigShouldersDisplay[wght].ttf | Tall condensed display, posters | JetBrains Mono |
| Archivo Black | archivoblack | ArchivoBlack-Regular.ttf | Heavy grotesque, single-word statements | Work Sans |
| Boldonse | boldonse | Boldonse-Regular.ttf | Ultra-wide expressive display (one word only) | Work Sans |
| Erica One | ericaone | EricaOne-Regular.ttf | Brush display, punk posters | Work Sans |
| Tektur | tektur | Tektur[wdth,wght].ttf | Technical display with width axis | Red Hat Mono |
| Jura | jura | Jura[wght].ttf | Thin futuristic sans | Red Hat Mono |
| Silkscreen | silkscreen | Silkscreen-Regular.ttf | Pixel display, retro marks | Space Mono |

## Using variable fonts

- In CSS: one `@font-face` with `font-weight: 100 900;` then any `font-weight` value works.
- In Pillow: `ImageFont.truetype(path, size)` loads the default instance (usually Regular). `font.set_variation_by_axes([700])` selects a weight when Pillow is built with FreeType variation support; `scripts/make_canvas.py` does this for the `weight` key and falls back silently otherwise. Static files (`-Regular`, `-Bold`) avoid the question.
- In reportlab: register the TTF with `TTFont("Inter", path)`; variable axes are ignored and the default instance is used.

## Choosing

- Posters with one big word: Big Shoulders Display, Archivo Black, Boldonse, Gloock.
- Quiet, observational, scientific: Instrument Serif with IBM Plex Mono, or Source Serif 4 with Inter.
- Warm editorial: Lora or Crimson Pro italic with Inter.
- Technical and cold: Tektur or Jura with Red Hat Mono; Space Grotesk with Space Mono.
- Navin house look: Inter 600 display, IBM Plex Mono marks, Lora when a serif voice is wanted.
