# Live avatars

The Live call shows a talking 3D avatar ([TalkingHead](https://github.com/met4citizen/TalkingHead), MIT)
whose body matches the agent's voice (`src/lib/live-avatar.ts`).

| File | Used for | Source and license |
| --- | --- | --- |
| `female.glb` | female, automatic and unknown voices | `mpfb.glb` from the TalkingHead repository, made with Blender and MPFB. **CC0**. Textures resized to 1024 px and converted to WebP. |
| `male.glb` | male voices | Generated with Blender and MPFB from CC0 MakeHuman assets (skin `young_caucasian_male2`, hair `short02`, clothes `male_casualsuit01`), CC0 visemes and ARKit face units, TalkingHead's MPFB rig (MIT). **CC0**. Recipe: `scripts/live-avatar/`. |

## Replacing an avatar

Drop any TalkingHead-compatible GLB under the same name, no code change (or regenerate with `scripts/live-avatar/`):

- Mixamo-compatible rig, root object named `Armature`;
- the 52 ARKit blend shapes (`jawOpen`, `eyeBlinkLeft`...) and the 15 Oculus visemes (`viseme_aa`, `viseme_O`...).

Check the license of every avatar: Ready Player Me, Avaturn, AvatarSDK and VRoid
example avatars are for non-commercial use only. Microsoft RocketBox models are
MIT (re-rig them in Mixamo, then rename the shape keys with TalkingHead's
`blender/rename-rocketbox-shapekeys.py`).

To keep the file light: `gltf-transform resize in.glb tmp.glb --width 1024 --height 1024`
then `gltf-transform webp tmp.glb out.glb`. Do not `quantize`: it drops the skin.
