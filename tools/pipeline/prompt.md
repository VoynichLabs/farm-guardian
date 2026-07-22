This is a snapshot from the {camera_name} camera at a small backyard flock in Hampton CT. Camera context: {camera_context}.

**Read the camera context first.** The `Camera context:` line above tells you exactly what this camera is pointed at and what subjects to expect. Trust it completely — do not invent subjects that aren't consistent with it. Let the context tell you whether you're looking at a nesting box, the coop, the run, or the open yard.

**The flock is mixed-age and mixed-breed.** Depending on the camera you may see anything from grown adult birds down to younger juveniles. Do not assume an age — read the bird in front of you.
- **Chickens** are the majority of the flock: a variety of rare, exotic, and standard breeds in strikingly varied plumage, at every stage from feathered juvenile to full adult. **Bantams** are the smallest chickens; standard breeds are mid-to-large. Small size alone does not mean "young" — a small bird may simply be a bantam adult.
- **Turkeys** may appear on some cameras: rounder, blockier bodies, thick heavy legs and large feet, a snood (fleshy nub above the beak), and bare pinkish facial skin. Heavier-built than chickens of the same age.

**Turkey vs. chicken:**
- **Round, wide, blocky body + thick legs + snood + bare pink/red facial skin** → turkey.
- **Finer-boned, feathered face, comb/wattles rather than a snood** → chicken.

**Camera angle.** Overhead coop/run views often show backs, tops of heads, and bird rumps rather than faces — that is normal, not a defect in the bird. Score conservatively for the budget sensors (usb-cam, gwtc) at awkward angles — a mid-band score is a good shot for those, and they rarely clear the 80+ gem bar. The s7-cam is the best sensor in the fleet, so score it on its own merits — it can produce genuine gems.

**Equipment is NOT birds.** The frame may contain a dome-shaped waterer, a feeder, a heat lamp, or other plastic/metal objects. These have NO head, NO beak, NO eyes, NO feathers. Do not count them as birds. If you are unsure whether something is a bird, look for a visible head and feathers — if absent, it is not a bird.

**Named individuals.** A couple of birds may be identified by name when their appearance clearly matches. Everyone else: use a generic label ("turkey," "bantam," "hen," or "young bird").

{named_individuals_block}

When in doubt, do not guess a name — use the generic type label. If the list above is empty, no bird in the current flock has a confirmed enough visual profile to name — use generic labels for everyone.

**Leg bands — the most reliable ID when you can read one.** Many of the named birds above now wear a small colored, numbered plastic band on one leg (usually the left). A clearly legible band is the single most reliable way to identify a bird — more reliable than plumage, since several of these birds are near-identical siblings. Use it by these rules, exactly:
- **Read the color AND the number, and match both** against the list above. Band colors look alike when the band is small or dimly lit (red vs. orange vs. pink; green vs. blue), so the number is what tells them apart — do not match on color alone.
- **Only mention a band you can actually SEE on the leg in THIS image.** Never infer or assume a band from a bird's plumage or from which bird you think it is. If the leg is hidden, turned away, too far, or too blurry to read the band, then say nothing about a band and fall back to a generic label — do not guess a band, a number, or a name.
- **A clearly legible band outranks plumage.** When the band plainly matches a named bird, you may say "likely [name]." A band you cannot read changes nothing — judge by plumage exactly as before, and stay with a generic label when unsure.
- Not every bird is banded, and an unbanded leg or a leg turned out of view is completely normal — it is not a defect and not a reason to lower the score.

**Coloration — describe it specifically, always.** These are rare-breed exotic birds and many are strikingly colored. When a bird is sharp in frame, describe the actual colors and markings you see: "chipmunk-striped brown-and-cream," "solid black," "blue-grey," "buff yellow," "black-and-gold laced," "rust-orange with dark wing tips," "barred black-and-white," "pale silver with a dark dorsal stripe." Do NOT write "colorful chick" or "distinctive markings" — name the actual colors. Every caption should tell someone exactly what color bird they'd see if they looked at this photo.

**Breed speculation — encouraged.** These birds came from Cackle Hatchery's Exotic Island Fowl Special and Rare Chick Special, so the flock is a mix of rare and exotic breeds. As birds mature, color patterns, crests, and head features become clearer. When you see a distinctive clue, speculate on the breed:
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
- `scene`: pick the best match for what you actually see in the image. Use the camera_context to help, but trust your eyes — `birdcatraz` for the outdoor enclosed poultry area (the fenced compound where the flock lives, including the water-bowl area and the ground around the coop and turkey pen); `coop` for the coop/run interior itself; `nesting-box` if you see a nest area; `yard` for open outdoor space OUTSIDE the enclosure; `brooder` only for an indoor heat-lamp brooder view (historical — no camera currently shows one); `other` if nothing fits.
- `bird_count`: count ONLY objects with a visible head, beak, or feathers. Waterers, feeders, and equipment are zero. If you can't confidently identify a head and body, don't count it.
- `individuals_visible`: use `"adult"` for a fully mature bird and `"chick"` for any clearly young bird (young turkeys and young chickens both qualify). Use `"unknown-bird"` when you can't tell the age or type. A mixed-age frame can contain both `"adult"` and `"chick"`.
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
  - **HARD PREREQUISITE for `strong` — the close-and-looking rule.** A frame is `strong` ONLY IF at least one bird is BOTH (a) **relatively close to the camera** — a foreground bird occupying a meaningful chunk of the frame, not a small/distant background bird — AND (b) **looking toward the lens** with at least one eye visible (direct or three-quarter, not a pure side profile where the bird is clearly looking elsewhere). BOTH conditions must hold for the SAME bird. If the closest, most prominent bird is facing away (back, rump, or tail to camera) and the only birds looking toward the lens are small or far off in the background, the frame is NOT `strong` — cap it at `decent` or `skip` no matter how sharp it is. This is the single most important gate; apply it before anything below.
    - **Do not let other factors compensate.** This is a precondition, not a weighted factor. If neither condition holds — no close bird, OR no bird looking at the lens — then sharpness, exposure, lighting, composition, plumage rarity, and bird count DO NOT MATTER and cannot pull the frame back up. A tack-sharp, beautifully lit, rare-breed wide shot where every bird is distant or turned away is still a `skip`. Check this gate FIRST, before you weigh anything else: hold `share_worth` at `decent` or `skip` when it fails, and score `expression_score` and `detail_score` honestly low (a distant or turned-away bird has little readable expression or detail) so the frame lands well under the gem bar.
  - **"strong"** — the frame must be genuinely usable/interesting as a farm gem. Use `strong` only when the hard prerequisite above is met AND at least one of these is true and none of the skip triggers apply:
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
    9. Multiple scattered birds around feeders, fence lines, shadows, or background clutter where one bird is merely standing while others peck/forage nearby — a wide inventory snapshot with no clear lead subject. **This is about DISTANT SCATTER, not the water bowl itself:** the s7 camera is deliberately aimed at the big water bowl, and a close, sharp bird drinking or facing the lens AT the bowl passes the close-and-looking rule and can absolutely be `strong`. Only demote when the birds are small, far, and unfocused with no lead subject.
    10. The closest, most prominent bird in the frame is facing away (back, rump, or tail to camera) — even if other birds farther back are visible or facing the lens. A big sharp foreground rump with only small distant faces behind it is a skip. The lead bird has to be looking at you.
  - **"decent"**: clear, in-focus frame with visible birds that doesn't hit a strong trigger and isn't killed by a skip trigger. Archive-worthy but not remarkable.
  - When in doubt between `decent` and `skip`, lean `skip`. When in doubt between `strong` and `decent`, lean `decent`.
- `share_reason`: one specific sentence about THIS frame — not a restatement of the rules. E.g., "Bronze turkey poult looking directly into the lens, left eye sharp" or "All three birds in motion, no sharp faces visible, minor blur throughout."
- `expression_score`: integer 0–30. **One of two components you score directly.** How visually striking, absurd, or expressive the bird's face and posture are. A bird doing something ridiculous scores high; a bird just standing there scores near 0.
  - **25–30**: a genuinely ridiculous or arresting moment — beak wide open mid-call, caught mid-blink, head cocked at a comical angle, mid-flap chaos, sparring, a big stretch, or a bird staring dead into the lens with a wild bright eye.
  - **15–24**: clear character — alert and craning, curious head-tilt, an obvious behavior with the face engaged.
  - **6–14**: mild — a calm bird with a visible but ordinary expression.
  - **0–5**: neutral or none — a bird just standing, drowsing, facing away, or no readable face.
- `detail_score`: integer 0–25. **The other component you score directly.** How much distinctive bird detail is actually visible AND in focus. Reward what you can really see: a sharp claw or foot, spread wing feathers, individual feather texture, a bright eye, comb/wattle/snood, vivid plumage pattern. Do NOT reward detail that is merely implied, distant, or soft.
  - **20–25**: several crisp features at once — e.g. a raised claw, feather-by-feather wing detail, and a sharp eye all readable.
  - **12–19**: one or two clear standout features in good focus.
  - **5–11**: some detail but soft, small, or partly obscured.
  - **0–4**: nothing distinctive readable — distant, blurred, or backside-only.
- `overall_score`: integer 0–100, the **farm-gem rating shown in Discord**. The pipeline RECOMPUTES this from four weighted components, so focus on scoring `expression_score` and `detail_score` accurately — still give your best 0–100 estimate here, but it will be recalculated. The four weighted axes (sum to 100): **frame dominance (0–30)** from `largest_subject_pct`, **expression (0–30)** = your `expression_score`, **notable detail (0–25)** = your `detail_score`, **technical quality (0–15)** from `image_quality`+`lighting`. A frame only reaches Discord at **80+**.
  - **What lands 80+:** a bird filling a large part of the frame (high dominance) that is ALSO doing something expressive/absurd AND showing crisp distinctive detail (a claw, a wing, a wild eye) in sharp focus. All four axes strong at once. This is intentionally hard.
  - **What stays under 80:** distant birds, neutral standing poses, soft focus, backsides, cluttered floor scenes, or any frame missing one of the four axes. A clean but ordinary profile portrait is a mid-band shot (~55–70), not a gem.
  - **Solo is not penalized.** One sharp bird filling the frame and facing the lens scores the same as a group of equal quality. Solitude is not a deficit.
  - **The 99%-er (95–100).** A bird filling the majority of the frame, making a ridiculous expression, with a monster claw or spread wing visible, razor-sharp. Reserve the very top of the scale for exactly this — it is the best shot the farm gets.
  - Keep this consistent with `share_worth`: only frames you tag `strong` should reach 80+; `decent` frames land mid-band; `skip` frames at the bottom.
  - **Calibration examples (score the components; the overall follows):**
    - Brooder Floor: flat floor snapshot, several birds pecking, partial bodies and backs, no clean face → `share_worth="skip"`, `expression_score=3`, `detail_score=4`.
    - Coop through wire mesh, distant/obscured, generic pecking, cluttered run → `share_worth="skip"`, `expression_score=2`, `detail_score=3`.
    - Distant feeder/bowl scatter: several small, far-off birds, one merely standing while others forage in shadows, no close lead subject → `share_worth="skip"`, `expression_score=4`, `detail_score=5`.
    - Water-bowl portrait: one bird close to the lens at the big water bowl, head dipped to drink or looking up at the camera with a sharp eye and water droplets visible → `share_worth="strong"`, `expression_score=20`, `detail_score=18` (the s7's signature shot — do not confuse it with distant scatter).
    - Close-and-looking failure — a large sharp buff bird fills the foreground but is rump-to-camera → fails the hard prerequisite → `share_worth="skip"`, `expression_score=2`, `detail_score=4`.
    - Clear profile portrait, one sharp bird, readable eye/plumage, clean background, but calm/neutral → `share_worth="strong"`, `expression_score=14`, `detail_score=16` (a nice archive shot, mid-band, not a gem).
    - Bird selfie close-up: direct eye contact, wild expression, a claw or spread wing visible, razor-sharp, fills most of the frame → `share_worth="strong"`, `expression_score=30`, `detail_score=24` (the 99%-er).
- `caption_draft`: 2–4 sentences, up to ~450 chars. **Describe the whole scene — all the birds, not just one.** This goes on social media. Make it vivid.
  - **Describe only the birds.** Do NOT mention the heat lamp, wood shavings, bedding, the nesting box walls, or the wooden structure. Those are background. The birds are the subject.
  - **Paint the full picture.** Who's where? One bird alone on the perch while others huddle below? Say so. A lone sentry above a pile of sleepers is a story — tell it.
  - **Colors for every visible bird.** "a white fluffy chick," "three dark mottled birds in black and grey," "a rust-and-brown bantam," "a barred black-and-white chick." Never "a colorful group."
  - **Expression and posture.** "looking directly at the camera," "eyes closed, heads tucked," "standing tall and alert," "pressed together, fast asleep."
  - **Species/type when distinguishable.** Turkeys vs. small bantams vs. larger standard chickens; note adult vs. young bird when it's clear.
  - **Leg band only when it's clearly legible.** If a bird wears a colored, numbered leg band that you can genuinely read in this frame, name it — "green leg band #2," "purple band #12." This only applies when the band is actually visible; never invent one or state a number you cannot read.
  - **Every caption must describe what is physically different in THIS frame.** You are seeing a new image each time — look at it fresh. What bird is closest? Which one is sharpest? Who has their face toward the camera right now? What are the specific colors you see? Do not reuse the same sentence structure or the same birds as a previous caption. If the birds are in the same pile, find a different individual to lead with, or describe the pile from a different angle.
  - Good examples: "A white chick stands alert in the center while three dark mottled birds — black-and-grey speckled — doze pressed together on the perch above, heads tucked in tight. A second white bird picks its way along the beam behind them." / "A chipmunk-striped bantam, possibly a Phoenix, locks eyes with the camera while two rust-orange birds sleep against it, faces buried."
  - Bad examples: "A white chick stands beneath the heat lamp on wood shavings." / "Birds sleeping in the nesting box." — mentions the lamp/shavings, describes only one bird.
- `concerns`: only populate if you see an injured bird, dead bird, abnormal posture, fighting beyond normal pecking, or an environmental hazard. Empty array otherwise.
