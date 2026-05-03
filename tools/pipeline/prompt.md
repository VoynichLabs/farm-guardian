This is a snapshot from the {camera_name} camera at a small backyard flock in Hampton CT. Camera context: {camera_context}.

**What this camera sees.** {camera_name} is mounted in the nesting box as of {today}, watching a mixed group of young birds:
- **2 bronze broad-breasted turkey poults**, approximately 7 days old. At this age they are noticeably LARGER than the chicken chicks — longer necks, bigger feet, taller upright stance, bare pinkish skin on the face and snood area. Their bodies are bulkier and their legs are proportionally long.
- **1 standard chicken chick**, approximately 28 days old. More feathered-out than the bantams, mid-sized — bigger than the bantams but smaller than the turkey poults.
- **Several exotic bantam chickens**, approximately 28 days old. The SMALLEST birds in the group. Varied and often striking plumage: stripes, crests, feathered feet, unusual color combinations.

**Species identification — read carefully before counting.** Turkey poults: large body, long neck, tall legs, bare pink facial skin, upright posture. Chicken chicks: rounder, fluffier-faced, shorter-legged. Bantams: noticeably small for their age. When you see a small, round, fluffy bird, it is almost certainly a chicken chick, not a turkey poult.

**Equipment is NOT birds.** The frame may contain a dome-shaped waterer, a feeder, a heat lamp, or other plastic/metal objects. These have NO head, NO beak, NO eyes, NO feathers. Do not count them as birds. If you are unsure whether something is a bird, look for a visible head and feathers — if absent, it is not a bird.

**No named individuals.** Do not refer to any bird by name. Use "turkey poult," "chicken chick," "bantam chick," or "young bird."

**Coloration — describe it specifically, always.** These are rare-breed exotic birds and many are strikingly colored. When a bird is sharp in frame, describe the actual colors and markings you see: "chipmunk-striped brown-and-cream," "solid black," "blue-grey," "buff yellow," "black-and-gold laced," "rust-orange with dark wing tips," "barred black-and-white," "pale silver with a dark dorsal stripe." Do NOT write "colorful chick" or "distinctive markings" — name the actual colors. Every caption should tell someone exactly what color bird they'd see if they looked at this photo.

**Breed speculation — encouraged at 4 weeks.** These chicks came from Cackle Hatchery's Exotic Island Fowl Special and Rare Chick Special. At ~4 weeks, distinguishing features are starting to appear. When you see them, speculate on the breed:
- **Feathered feet/legs** → likely a Cochin (most common in the Rare Chick Special), or Sultan
- **Head crest or pouf forming** → likely Polish, Sultan, Houdan, or Spitzhauben
- **Chipmunk-striped (brown/cream dorsal stripes)** → likely Phoenix, Yokohama, or Red Jungle Fowl (Exotic Island Fowl)
- **Mostly or all black** → likely Black Sumatra or Ayam Cemani
- **Reddish-brown/buff tones, game-bird build** → likely Cubalaya or Jungle Fowl
- **Golden or silver duckwing pattern** → likely Golden or Silver Duckwing Phoenix/Yokohama
- **Buff/cream, heavy build** → likely Buff Cochin
- **Grey/blue-grey, fluffy** → likely Blue or Splash Cochin, or Blue Polish if a crest is forming
When speculating, say "possibly a [breed]" or "likely a [breed] — note the feathered feet." You won't always be certain and that's fine — speculate when the features are there.

**Expression and demeanor.** When a bird's face is visible and in focus, describe what it looks like: alert and upright, curious with head tilted, drowsy with half-closed eyes, startled with neck stretched, calm and relaxed. A bird staring directly into the lens with bright open eyes is the best shot the farm gets — say so specifically.

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
- `share_worth`: sharpness + a visible face is the primary bar. A blurry bird or a bird's rear end is worthless regardless of other qualities.
  - **"strong"** — ANY of these, and none of the skip triggers below:
    1. At least one bird looking DIRECTLY at the camera with a sharp, visible eye. This is the best shot the farm gets — prioritize it every time.
    2. A sharp bird face in profile or three-quarter view where the eye and beak are clearly visible and the plumage detail is crisp — individual feather edges, down texture, color markings readable at the pixel level.
    3. A clear active-behavior moment where the subject is sharp: sparring, mid-wing-flap, mid-stretch, drinking, eating. The bird's face or body detail must be in focus — blurry action is still blurry.
  - **"skip"** — ANY of these demotes the frame:
    1. Every bird in frame is blurry, smeared, or unrecognizable.
    2. No birds in frame, or activity=none-visible.
    3. Every bird facing fully away — only backs, tails, or rear ends visible, no face or eye on any subject.
    4. A pile of indistinct fluff where no individual bird is distinguishable and no face is visible. A blob of feathers is not a photo.
  - **"decent"**: clear, in-focus frame with visible birds that doesn't hit a strong trigger and isn't killed by a skip trigger. A sideways group shot with some faces partially visible is decent. Archive-worthy but not remarkable.
  - When in doubt between `decent` and `skip`, lean `skip`.
- `share_reason`: one specific sentence about THIS frame — not a restatement of the rules. E.g., "Bronze turkey poult looking directly into the lens, left eye sharp" or "All three birds in motion, no sharp faces visible, minor blur throughout."
- `caption_draft`: one or two sentences, up to ~200 chars. Lead with the sharpest, most interesting bird in frame — its colors, its expression, what it's doing.
  - **Colors first.** If a bird is sharp, describe its actual coloration: "a chipmunk-striped chick in brown and cream," "a rust-and-black bantam," "a solid white crested chick," "a barred black-and-white bird." Never write just "a colorful chick" — say what colors.
  - **Expression when visible.** If you can see a face clearly: "staring directly into the lens," "head tilted, one eye fixed on the camera," "alert with neck stretched," "drowsy, eyes half-closed." These details are what make the caption worth reading.
  - **Species when distinguishable.** "a bronze turkey poult," "the larger standard chick," "a bantam chick."
  - **Action.** What is the bird doing? Pecking, drinking, preening, stretching, sparring, standing still?
  - **Every caption must be different.** Find a different lead detail each time — a different bird, a different color, a different expression or action.
  - Good examples: "A chipmunk-striped chick — possibly a Phoenix — stares directly into the lens, one bright eye sharp in focus." / "Two bronze turkey poults crowd the waterer while a blue-grey chick with a developing crest, likely Polish, stands watching behind them." / "A rust-and-black bantam mid-stretch, one wing fanned wide, head turned alertly toward the camera." / "A buff-yellow chick with visibly feathered feet — probably a Buff Cochin — sits relaxed in the foreground, eyes half-closed."
  - Bad examples: "Chicks in the nesting box." / "Young birds eating." / "Colorful chicks." / "A distinctive-looking chick." — no color, no breed hint, no expression, no action.
- `concerns`: only populate if you see an injured bird, dead bird, abnormal posture, fighting beyond normal pecking, or an environmental hazard. Empty array otherwise.
