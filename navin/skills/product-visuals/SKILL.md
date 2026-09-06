---
name: product-visuals
description: Generate product imagery - packshots, lifestyle scenes, e-commerce sets, mockups, and short product videos from reference photos. Use for product pages, catalogs, marketplaces, and launches.
metadata: {"navin":{"emoji":"📦","category":"marketing"}}
---

# Product Visuals

Produce a consistent visual set for one product: packshots, lifestyle scenes, detail shots, and short videos - from a text description or from the user's real product photos.

## AWS media templates

If a media template is attached, treat the downloaded file under `.navin/resources/media-templates/` as the master reference (format, lighting, crop). Generate variations from that file. If the download is missing, stop and tell the user.

## Tooling

- **`generate_image`** - all still visuals. Pass the user's product photo (or a validated generated packshot) in `reference_images` to keep the product identical across the whole set.
- **`generate_video`** - 360 turns, hero animations, unboxing-style clips; use `reference_image` with the validated packshot.
- **`visual_qa`** - mandatory final gate for every still. Pass the authoritative
  product photo in `references` and claim `product_fidelity`; use marketplace,
  ratio, dimensions, alpha and safe-zone requirements for the placement.
- **`exec` + Pillow** - exact marketplace sizes, white-background compliance checks, batch renaming.
- **Interactive 3D** (user asked to orbit the product on a page): `three` or `@react-three/fiber` + `@react-three/drei` on that web page, with the validated packshot as the still fallback. Never put that canvas on a PowerPoint slide.
- Missing tools → deliver the shot list + exact prompts and point to Settings → Image / Video.

## The standard e-commerce set

For one product, produce in this order:

1. **Master packshot** - white/neutral background, soft studio lighting, slight shadow. Validate with the user before declining anything.
2. **Angles** - 3/4 left, 3/4 right, back, top (each via `reference_images` from the master).
3. **Detail shots** - texture, label, ports/seams - whatever sells the product.
4. **Lifestyle scenes** - product in real context, matching the brand's world (2-3 scenes).
5. **Scale shot** - product next to a familiar object or in-hand.
6. **Short video** - 6-8s rotation or hero animation from the master packshot.

## Prompt patterns

Packshot:

```text
generate_image(
  prompt="Professional e-commerce packshot of [product], pure white background, soft even studio lighting, subtle contact shadow, centered, no props, ultra sharp product photography",
  reference_images=["/path/to/user-product-photo.jpg"],
  aspect_ratio="1:1", image_size="2K"
)
```

Lifestyle:

```text
generate_image(
  prompt="Use the reference image. Place this exact product on a rustic oak kitchen table, morning sunlight through a window, shallow depth of field, warm lifestyle photography, keep the product and its label strictly unchanged",
  reference_images=["<master packshot path>"],
  aspect_ratio="4:3"
)
```

360/hero video:

```text
generate_video(
  prompt="Slow elegant 360-degree rotation of this exact product on a seamless white background, studio lighting, keep product and label unchanged",
  reference_image="<master packshot path>",
  aspect_ratio="1:1", duration_seconds=8
)
```

Always include "keep the product and its label strictly unchanged" when working from references.

## Marketplace compliance (check before delivering)

| Platform | Main image | Notes |
|---|---|---|
| Amazon | pure white (RGB 255,255,255), product ≥85% of frame, ≥1600px | no text, logo, watermark |
| Shopify/site | free, 1:1 or 4:5 recommended, ≥2048px | consistent set |
| Instagram Shop | 1:1, lifestyle allowed | product visible, low text |

Use Pillow via `exec` to verify pixel dimensions and background whiteness, and to export exact sizes.
Then call `visual_qa` for each deliverable. Deliver only PASS assets. Rework WARN
assets and never deliver BLOCK assets automatically. A product-fidelity claim
without an authoritative reference is BLOCK, never PASS.

## Rules

- One master, validated early - everything declines from it via `reference_images`.
- Never alter the product itself: shape, colors, label, and branding stay exact.
- Deliver via the `message` tool with artifact paths in `media`; keep raw paths internal.
- Name files predictably: `sku_packshot_front.png`, `sku_lifestyle_kitchen.png`, etc.
- For ad-oriented declinations (headline space, platform ratios, CTA), continue with `ad-creative-generator`.
