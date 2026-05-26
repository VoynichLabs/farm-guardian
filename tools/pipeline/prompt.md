This is a snapshot from the {camera_name} camera at a small backyard flock in Hampton CT. Camera context: {camera_context}.

**Read the camera context first.** The `Camera context:` line above tells you exactly what this camera is pointed at and what subjects to expect. Trust it completely — do not invent subjects that aren't consistent with it. If the context says "coop" or "overhead" or "older birds," you are NOT looking at turkey poults or young bantam chicks. If the context says "nesting box," you ARE.

**Nesting box cameras (s7-cam, mba-cam when watching the nesting box)** — the young flock as of {today}:
- **2 bronze broad-breasted turkey poults**, ~7 days old: rounder/puffier bodies, thick heavy legs with large feet, a snood (fleshy nub above the beak), bare pinkish facial skin, upright blocky stance. Larger than the chicken chicks.
- **1 standard chicken chick**, ~28 days old. More feathered-out than the bantams, mid-sized.
- **Several exotic bantam chickens**, ~28 days old. The SMALLEST birds. Varied and striking plumage.

**Turkey vs. chicken (nesting box cameras only):**
- **Round, wide, blocky body + thick legs + snood + bare pink face** → turkey poult.
- **Fluffy, fine-boned, feathered face** → chicken chick.

**Coop/overhead cameras (usb-cam, gwtc)** — you are seeing older birds:
- **Adult chickens** (fully grown, mixed breeds) and **older feathered chicks** that have graduated from the brooder. More adult-shaped, fully feathered. No turkey poults here.
- The angle may be overhead — expect to see backs, tops of heads, and bird rumps rather than faces. That is normal for these cameras.
- Score conservatively: these are lower-quality sensors at awkward angles. A 6 is a good shot here.

**Equipment is NOT birds.** The frame may contain a dome-shaped waterer, a feeder, a heat lamp, or other plastic/metal objects. These have NO head, NO beak, NO eyes, NO feathers. Do not count them as birds. If you are unsure whether something is a bird, look for a visible head and feathers — if absent, it is not a bird.

**Named individuals.** The following birds may be identified by name when their appearance matches. All others: use "turkey poult," "chicken chick," "bantam chick," or "young bird."

- **Birdadotta** (EE × RIR cross, b. 2026-04-25, ~bantam-adjacent size): blue/grey eyes; distinctive coloring around the beak; slight rust-colored feathers emerging on the wings; slight white tips on the wings; white feathers on the belly. Often has a small teal/turquoise bead or marker visible on her. When you see a chick matching this profile, you may say "likely Birdadotta" in the caption.
- **Birdadette** (Easter Egger, b. 2026-04-06, LARGER than Birdadotta — 19 days older): gray-brown down with golden-rust chest feathers coming in; **yellow legs** (key tell); **white rump/tail fan** visible from behind or in profile; no white belly feathers. Very social/forward — often at the feeder. May be identified as "likely Birdadette" when the profile matches.

When in doubt, do not guess a name — use the generic type label.

**Coloration — describe it specifically, always.** These are rare-breed exotic birds and many are strikingly colored. When a bird is sharp in frame, describe the actual colors and markings you see: "chipmunk-striped brown-and-cream," "solid black," "blue-grey," "buff yellow," "black-and-gold laced," "rust-orange with dark wing tips," "barred black-and-white," "pale silver with a dark dorsal stripe." Do NOT write "colorful chick" or "distinctive markings" — name the actual colors. Every caption should tell someone exactly what color bird they'd see if they looked at this photo.

**Breed speculation — encouraged at 4 weeks.** These chicks came from Cackle Hatchery's Exotic Island Fowl Special and Rare Chick Special. At ~4 weeks, color patterns and head features are becoming visible. When you see a distinctive clue, speculate on the breed:
- **Head crest or pouf forming** → likely Polish, Houdan, or Spitzhauben
- **Chipmunk-striped (brown/cream dorsal stripes)** → likely Phoenix, Yokohama, or Red Jungle Fowl (Exotic Island Fowl breeds)
- **Solid or near-solid black with NO visible lacing, patterning, or golden/rust highlights, clean-legged** → likely Black Sumatra or Ayam Cemani. Do NOT use this if you see any golden, amber, or rust lacing on the wings or chest.
- **Dark/black base coat WITH golden or amber lacing on wings or chest** → likely Easter Egger cross, Golden-Laced Wyandotte cross, or golden-laced variety — not a Black Sumatra
- **Reddish-brown/buff tones, lean game-bird build** → likely Cubalaya or Jungle Fowl
- **Golden or silver duckwing pattern** → likely Golden or Silver Duckwing Phoenix/Yokohama
- **Buff/cream coloring** → possibly Buff variety of a Rare Chick breed
- **Grey/blue-grey** → possibly Blue or Splash variety; if a crest is also forming, likely Polish
When speculating, say "possibly a [breed]" or "likely a [breed]." You won't always be certain and that's fine — speculate when the visual clues are there.

**Expression and demeanor.** When a bird's face is visible and in focus, describe what it looks like: alert and upright, curious with head tilted, drowsy with half-closed eyes, startled with neck stretched, calm and relaxed. A bird staring directly into the lens with bright open eyes is the best shot the farm gets — say so specifically.

Guidance on specific fields:
- `scene`: pick the best match for what you actually see in the image. Use the camera_context to help, but trust your eyes — if it looks like a brooder, call it brooder; nesting-box if you see a nest area; yard if open outdoor space; coop for the run/coop interior; other if nothing fits.
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
- `share_worth`: this is the **farm-gem gate**, not just a visibility flag. Sharpness + a visible face is necessary, but not sufficient for `strong`. A blurry bird, a bird's rear end, a bird hidden by wire mesh, or a flat floor-pecking snapshot is worthless regardless of how many birds are present.
  - **"strong"** — the frame must be genuinely usable/interesting as a farm gem. Use `strong` only when at least one of these is true and none of the skip triggers apply:
    1. A clean, sharp subject looking DIRECTLY at the camera with a visible eye. This is the best shot the farm gets — prioritize it every time.
    2. A sharp face in profile or three-quarter view where the eye/beak are clearly visible, feather detail is crisp, and the subject is not hidden by wire, clutter, or other birds.
    3. A standout behavior/story moment: sparring, wing-flap, stretch, dust-bath, drinking/eating with the face and body cleanly visible, or a clear interaction between birds. **Generic pecking at the floor is NOT standout behavior.**
    4. Strong composition/light: a clean portrait, striking color/pattern, or a frame that would plausibly stop someone scrolling.
  - **"skip"** — ANY of these demotes the frame:
    1. Every bird in frame is blurry, smeared, or unrecognizable.
    2. No birds in frame, or activity=none-visible.
    3. Every bird facing fully away — only backs, tails, or rear ends visible, no face or eye on any subject.
    4. A pile of indistinct fluff where no individual bird is distinguishable and no face is visible. A blob of feathers is not a photo.
    5. Wire mesh/fencing/cage bars dominate the view, cut across the subjects, or make the birds feel obstructed.
    6. Cluttered brooder/coop floor: feeders, waterers, bedding, walls, shadows, or random floor texture dominate more than the birds.
    7. Partial/obscured birds, cropped-off bodies, distant small birds, mostly backsides, or birds blocked by other birds/equipment.
    8. Generic static eating/foraging/pecking on the brooder or coop floor with no clean subject, no eye contact, no interesting posture, and no story/action.
    9. Multiple scattered chicks/birds around water bowls, feeders, fence lines, shadows, or background clutter where one bird is merely standing while others peck/forage nearby. That is a coop-floor inventory snapshot, not a gem.
  - **"decent"**: clear, in-focus frame with visible birds that doesn't hit a strong trigger and isn't killed by a skip trigger. Archive-worthy but not remarkable.
  - When in doubt between `decent` and `skip`, lean `skip`. When in doubt between `strong` and `decent`, lean `decent`.
- `share_reason`: one specific sentence about THIS frame — not a restatement of the rules. E.g., "Bronze turkey poult looking directly into the lens, left eye sharp" or "All three birds in motion, no sharp faces visible, minor blur throughout."
- `overall_score`: integer 0–10. This is the **farm-gem rating shown in Discord**, so score the whole photo: subject clarity, composition, story/behavior, and whether it is actually worth sharing. Do NOT score merely on bird visibility. Most routine camera frames should land in the 2–5 range. 6+ is intentionally uncommon.
  - **What raises the score:** clean sharp subject; visible eye/face; vivid color/plumage detail; birds filling a meaningful portion of the frame; strong composition/light; direct eye contact; standout behavior or a clear little story.
  - **What lowers the score:** blur, soft focus, motion smear; wire mesh/fencing/cage bars over the birds; cluttered floor/background; partial or cropped birds; birds facing away; backsides/rear ends; distant small birds; generic pecking/eating/foraging with no clear subject; compression artifacts.
  - **Static brooder/coop-floor calibration:** floor-level or overhead snapshots of birds pecking/eating/foraging in the brooder or coop default to **2–4** unless there is a clean standout subject/action/composition. Merely visible chickens are not a 6.
  - **Solo portrait rule: a single bird posing alone is not penalized for being alone.** One sharp bird filling a meaningful portion of the frame and facing the camera scores the same as a group shot of equivalent quality. A solo close-up portrait with a clear eye and readable plumage can reach 8–9. Solitude is not a deficit.
  - **The bird selfie is a 10.** A bird filling the majority of the frame, staring directly into the lens, razor-sharp — that is a 10. Not a 7. Not an 8. If a bird posed for its portrait — close-up, direct eye contact, sharp — score it a 10.
  - **Activity matters only when it creates a story.** Sparring, wing-flap, stretch, dust-bathing, drinking, or eating can raise the score if the subject is sharp and cleanly framed. Routine floor pecking does not raise the score.
  - **0–2**: No birds, empty frame, totally unusable, heavy blur/artifacts, or only tiny/obscured birds.
  - **3–4**: Birds present but mostly backs, partial bodies, wire-obstructed, distant, flat floor snapshots, or generic pecking with little interest visible. This is the expected range for routine brooder/coop-floor pecking scenes.
  - **5**: One or more birds visible with some face/detail, ordinary sharpness, acceptable but not a gem.
  - **6**: A genuinely usable/interesting farm-gem shot: clear subject, good sharpness, readable face/plumage, and decent composition or a small story. **Not** just visible chickens.
  - **7**: Standout animal behavior, clean subject, strong composition/light, direct eye contact, or vivid subject detail. Better than a normal usable frame.
  - **8**: Bird(s) filling the frame, faces prominent, vivid plumage detail readable, strong composition/story — solo portrait or group equally eligible.
  - **9**: Exceptional sharpness AND composition — striking plumage detail, face prominent, multiple birds sharp or one large bird with striking color and pattern. The kind of frame that stops someone mid-scroll.
  - **10**: The farm's best shot. Either: (a) a bird selfie — one bird close-up, filling the majority of the frame, looking DIRECTLY into the lens, razor-sharp; OR (b) a group portrait where 3 or more birds are all sharp, faces toward the camera, vivid plumage detail readable on each. Reserve 10 for genuinely outstanding shots that hit one of these two bars.
  - This score must be consistent with `share_worth`: 0–4 → skip; 5–6 → decent; 7–10 → strong.
  - **Calibration examples:**
    - Bad prior 6/10 case — Brooder Floor: flat floor snapshot, several birds pecking/eating, partial bodies and backs, no clean face, no standout action/story → `share_worth="skip"`, `overall_score=3`.
    - Bad prior 6/10 case — Coop: chickens behind/through wire mesh, distant or partially obscured, generic pecking, cluttered run/floor dominates → `share_worth="skip"`, `overall_score=3`.
    - Bad prior 6/10 case — Water bowl / feeder scatter: a white chick stands alert near a water bowl, a speckled chick pecks at the ground, a blue-grey bird passes a feeder, and other birds forage in shadows under the fence line → `share_worth="skip"`, `overall_score=3`.
    - Ordinary visible chickens pecking on a clean floor, sharp but no eye contact/story → `share_worth="skip"` or `"decent"`, `overall_score=4`.
    - Clear profile portrait of one sharp bird with readable eye/plumage and clean background → `share_worth="strong"`, `overall_score=7`.
    - Bird selfie close-up, direct eye contact, razor-sharp, subject fills most of frame → `share_worth="strong"`, `overall_score=10`.
- `caption_draft`: 2–4 sentences, up to ~450 chars. **Describe the whole scene — all the birds, not just one.** This goes on social media. Make it vivid.
  - **Describe only the birds.** Do NOT mention the heat lamp, wood shavings, bedding, the nesting box walls, or the wooden structure. Those are background. The birds are the subject.
  - **Paint the full picture.** Who's where? One bird alone on the perch while others huddle below? Say so. A lone sentry above a pile of sleepers is a story — tell it.
  - **Colors for every visible bird.** "a white fluffy chick," "three dark mottled birds in black and grey," "a rust-and-brown bantam," "a barred black-and-white chick." Never "a colorful group."
  - **Expression and posture.** "looking directly at the camera," "eyes closed, heads tucked," "standing tall and alert," "pressed together, fast asleep."
  - **Species when distinguishable.** Turkey poults vs. bantam chicks vs. the larger standard chick.
  - **Every caption must describe what is physically different in THIS frame.** You are seeing a new image each time — look at it fresh. What bird is closest? Which one is sharpest? Who has their face toward the camera right now? What are the specific colors you see? Do not reuse the same sentence structure or the same birds as a previous caption. If the birds are in the same pile, find a different individual to lead with, or describe the pile from a different angle.
  - Good examples: "A white chick stands alert in the center while three dark mottled birds — black-and-grey speckled — doze pressed together on the perch above, heads tucked in tight. A second white bird picks its way along the beam behind them." / "A chipmunk-striped bantam, possibly a Phoenix, locks eyes with the camera while two rust-orange birds sleep against it, faces buried."
  - Bad examples: "A white chick stands beneath the heat lamp on wood shavings." / "Birds sleeping in the nesting box." — mentions the lamp/shavings, describes only one bird.
- `concerns`: only populate if you see an injured bird, dead bird, abnormal posture, fighting beyond normal pecking, or an environmental hazard. Empty array otherwise.
