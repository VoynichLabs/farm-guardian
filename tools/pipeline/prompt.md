This is a snapshot from the {camera_name} camera at a small backyard flock in Hampton CT. Camera context: {camera_context}.

**What this camera sees.** {camera_name} is mounted in the nesting box as of {today}, watching a mixed group of young birds:
- **2 bronze broad-breasted turkey poults**, approximately 7 days old. At this age they are noticeably LARGER than the chicken chicks — longer necks, bigger feet, taller upright stance, bare pinkish skin on the face and snood area. Their bodies are bulkier and their legs are proportionally long.
- **1 standard chicken chick**, approximately 28 days old. More feathered-out than the bantams, mid-sized — bigger than the bantams but smaller than the turkey poults.
- **Several exotic bantam chickens**, approximately 28 days old. The SMALLEST birds in the group. Varied and often striking plumage: stripes, crests, feathered feet, unusual color combinations.

**Species identification — read carefully before counting.** Turkey poults: large body, long neck, tall legs, bare pink facial skin, upright posture. Chicken chicks: rounder, fluffier-faced, shorter-legged. Bantams: noticeably small for their age. When you see a small, round, fluffy bird, it is almost certainly a chicken chick, not a turkey poult.

**Equipment is NOT birds.** The frame may contain a dome-shaped waterer, a feeder, a heat lamp, or other plastic/metal objects. These have NO head, NO beak, NO eyes, NO feathers. Do not count them as birds. If you are unsure whether something is a bird, look for a visible head and feathers — if absent, it is not a bird.

**No named individuals.** Do not refer to any bird by name. Use "turkey poult," "chicken chick," "bantam chick," or "young bird."

Guidance on specific fields:
- `scene`: use `"nesting-box"` for frames from this camera.
- `bird_count`: count ONLY objects with a visible head, beak, or feathers. Waterers, feeders, and equipment are zero. If you can't confidently identify a head and body, don't count it.
- `individuals_visible`: use `"chick"` for any young bird (turkey poults and chicken chicks both qualify). Use `"adult"` only for a fully mature bird. Use `"unknown-bird"` for edge cases.
- `any_special_chick`: true if any individual bird has a visually notable feature — striking coloration, markedly different size, unusual posture, or anything that makes them stand out. Given the rare-breed bantam mix, err toward true. False only for a uniformly indistinct group.
- `apparent_age_days`: best estimate for the most prominent birds visible. -1 only if no birds are present.
- `activity`: what the majority of visible birds are doing. "none-visible" if no birds in frame.
- `image_quality`: judge on focus and motion, not on resolution. A 1080p webcam frame can be "sharp."
  - `sharp`: subjects are crisp and well-focused; feather edges visible on nearby birds; no motion smear.
  - `soft`: mild defocus or motion blur; subjects recognizable but lacking texture.
  - `blurred`: heavy motion blur, defocus beyond recognition, OR compression artifacts (banding, blocky regions, streaked/smeared pixel columns, colored fringes that don't match object edges). Artifacts always disqualify from `sharp`.
  - Fixed-focus close-up failure (bird too close for the lens): `soft` if recognizable as a bird, `blurred` if not.
- `bird_face_visible`: true if at least one bird's eye, beak, or facial profile is visible — including partial or side-on views. False only when every bird is fully turned away with no head detail.
- `subject_coverage_pct`: percent of the total frame area covered by birds. Exclude bedding, walls, feeders, waterers, heat lamp.
- `largest_subject_pct`: percent of the frame covered by the single largest bird only.
- `share_worth`: lead with sharpness and behavior — these are the primary criteria.
  - **"strong"** — ANY of these, and none of the skip triggers below:
    1. At least one bird looking DIRECTLY at the camera with a visible eye. Eye contact is the best shot the farm gets.
    2. Crisp feather or plumage detail on a bird filling a meaningful portion of the frame — individual feather edges, down texture, color gradation visible at the pixel level.
    3. A clear behavior moment: sparring, mid-wing-flap, mid-stretch, dust-bathing, drinking, eating, or any posture that reads as active and intentional. The subject must be sharp.
  - **"skip"** — ANY of these demotes the frame regardless of other qualities:
    1. All subjects blurry, smeared, or unrecognizable.
    2. No birds in frame, or activity=none-visible.
    3. Every bird facing fully away with no face visible.
    4. A uniform huddle-pile with no individual distinguishable and no notable posture.
  - **"decent"**: clear, in-focus frame that doesn't hit a strong trigger and isn't killed by a skip trigger. Archive-worthy but unremarkable.
  - When in doubt between `decent` and `skip`, lean `skip`.
- `share_reason`: one specific sentence about THIS frame — not a restatement of the rules. E.g., "Bronze turkey poult looking directly into the lens, left eye sharp" or "All three birds in motion, no sharp faces visible, minor blur throughout."
- `caption_draft`: one or two sentences, up to ~200 chars. Be SPECIFIC and OBSERVATIONAL — describe what is actually in this frame.
  - Name the species when you can tell them apart: "a bronze turkey poult," "a bantam chick," "the larger standard chick."
  - Lead with the most interesting thing in the frame: eye contact, a striking color pattern, a behavior, an unusual posture.
  - Include visible detail: feather/down color and markings, what the bird is doing, where in the frame it is.
  - **Every caption must be different.** If the scene looks similar to a previous frame, find a different specific detail to lead with — a different bird, a different angle, a different action.
  - Good examples: "A bronze turkey poult stares directly into the lens, its bare pink snood clearly visible." / "Three bantam chicks crowd the waterer while the larger standard chick stands apart at the back." / "A small black-and-white bantam chick mid-stretch, one wing fanned out to the right."
  - Bad examples: "Turkey poults in the nesting box." / "Chicks eating." / "Young birds in the brooder." — too generic, no specific detail.
- `concerns`: only populate if you see an injured bird, dead bird, abnormal posture, fighting beyond normal pecking, or an environmental hazard. Empty array otherwise.
