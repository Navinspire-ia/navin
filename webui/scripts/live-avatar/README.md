# Live avatar generation

`public/avatars/male.glb` is generated here with Blender and MPFB, headless,
following TalkingHead's `blender/MPFB/MPFB.md`. Every asset is CC0.

## Setup (once)

1. Blender 5.0 (tested with 5.0.1) and the MPFB extension (tested with 2.0.17):
   `blender -b --command extension install-file -r user_default -e add-on-mpfb-v2.0.17.zip`
2. MakeHuman asset packs, unzipped into MPFB's user data
   (`~/.config/blender/5.0/extensions/.user/user_default/mpfb/data`), all CC0:
   `makehuman_system_assets_cc0`, `hair01_cc0`, `shirts01_cc0`, `pants01_cc0`,
   `skins01_cc0`, `shoes01_cc0`, and the functional packs `visemes02` and
   `faceunits01` (Meta visemes and ARKit face units, CC0, by Mika Suominen).
   Downloads: http://static.makehumancommunity.org/assets/assetpacks.html
3. TalkingHead's MPFB rig (MIT, github.com/met4citizen/TalkingHead,
   `blender/MPFB/`): copy `talkinghead.mpfbskel` to `data/rigs/talkinghead.json`
   with `"identifying_bones": ["LeftToe_End"]` added, and `talkinghead.mhw` to
   `data/rigs/weights.talkinghead.json`. Keep `talkinghead-addon.py` at hand
   (path in `TALKINGHEAD_ADDON`).

## Generate

```bash
TALKINGHEAD_ADDON=/path/to/talkinghead-addon.py \
  blender -b --python gen_avatar.py -- male-raw.glb male.json
gltf-transform resize male-raw.glb tmp.glb --width 1024 --height 1024
gltf-transform webp tmp.glb ../../public/avatars/male.glb --quality 85
```

`male.json` picks the body (MPFB macros), skin, hair, brows, lashes and clothes
by file name; edit it for another look. Eyes get alpha clip 0.5 (the cornea must
not hide the iris), hair 0.3, brows and lashes 0.15; do not `quantize` the GLB
(it drops the skin).
