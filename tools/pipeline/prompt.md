This is a snapshot from the {camera_name} camera at a small backyard flock in Hampton CT. Camera context: {camera_context}.

**Location → age mapping.** The farm has two physically-separate bird groups and camera location tells you which you're seeing:
- **Brooder cameras (`usb-cam`, `mba-cam`, `s7-cam`, `iphone-cam`)**: YOUNGER chicks, 1–3 weeks old, plus Birdadette.
- **Coop cameras (`gwtc`)**: OLDER chicks (the previous brooder cohort that has graduated to the coop run) and the four winter-survivor adults.
- **Yard camera (`house-yard`)**: free-range adults or nothing; no chicks.

Use camera_name to decide which group you're looking at — don't guess age from apparent size alone.

Known birds in this flock as of {today}:
- Birdadette: 3rd-generation Easter Egger × Rhode Island Red mix, GPU-incubated, hatched 2026-04-06. Currently a CHICK (~1 week old), about 2 days older than the other brooder chicks so she's slightly larger. Solid BLACK body plumage with an ORANGE face. Adult plumage will differ from chick plumage — judge by current appearance only.
- Four winter-survivor adults (mixed breeds — not individually distinct in most photos)
- ~22 brooder chicks, currently 1-3 weeks old, mixed exotic-island-fowl variants and rare breeds. Most are visually similar; flag anything that stands out from its siblings.
- Older-chick cohort in the coop (previous brooder group): larger, more feathered-out, starting to look adult-shaped. Separate from the current brooder chicks.

Guidance on specific fields:
- `scene`: pick the best match for what's visible; "other" only if nothing else fits.
- `bird_count`: your best estimate; 0 if none visible.
- `individuals_visible`: only include what you can actually see. "birdadette" only if you see a brooder chick that is solid BLACK with an ORANGE face and is noticeably LARGER than the other chicks around her — that combination is unique to her in the brooder right now. "chick" for any other bird that looks like a chick (fluffy, small, brooder-aged). "adult-survivor" for any mature bird. "unknown-bird" for edge cases.
- `any_special_chick`: true only if one chick is visually distinct from the others in the frame (unusual coloring, markedly different size, unusual posture).
- `bird_face_visible`: true if at least one bird's face, eye, beak, or profile is visible to the camera — including partial views, slight head-turns, and birds where you can see the side of the head. False only when every bird in frame is clearly turned fully away (back, tail, rear) with no head detail at all. This flag matters for single-bird shots where a lone rear-view is not a gem; group-shot framing is judged separately. Neutral default: true for most frames with recognizable birds; false only for clear "fluffy ass, no head" solo compositions.
- `apparent_age_days`: -1 for non-chick scenes (this means "not applicable"). For chicks, your best guess 1-60.
- `activity`: what the majority of visible birds are doing. "none-visible" if no birds in frame.
- `image_quality`: judge relative to the image's native resolution, NOT against an ideal high-resolution camera. A 720p or 1080p webcam frame can absolutely be "sharp" — sharpness is about focus and motion, not megapixels. Use "sharp" whenever the subjects are crisp and well-focused at the resolution given; feather edges, individual feathers visible on nearby birds, clear boundaries between objects. Use "soft" only for genuine focus softness or mild motion blur where details are hazy. Use "blurred" only when subjects are unidentifiable due to motion or heavy defocus. Do NOT penalize images for being low resolution.
  - **Compression artifacts count as "blurred".** If you see vertical or horizontal banding, smeared/duplicated pixel columns or rows, blocky regions where the same color repeats in a grid, colored fringes that don't match object edges, or long uniform stripes running through the scene, tag the image as `blurred` regardless of how "sharp" individual edges look. These are H.264/H.265 decode errors from keyframe loss (common on the `gwtc` camera when the laptop is moved) — they are not photographic sharpness and should never be rated `sharp` or `soft`. They also disqualify the frame from `strong`.
  - **Fixed-focus close-ups count as "blurred" or "soft".** Some cameras (especially `gwtc` and `mba-cam`) have fixed focus. A bird positioned closer than the camera's minimum focal distance will appear as a soft colored blob with no visible feather texture. If the nearest bird's feathers are indistinct even though the rest of the scene looks fine, that's a close-focus failure — tag as `soft` if still recognizable as a bird, `blurred` if not.
- `share_worth`: tight rules, apply in order.
  - **"strong" triggers (ANY of these, and none of the skip conditions below):**
    1. A bird looking DIRECTLY at the camera with a clear visible face — eye contact, beak pointing roughly toward the lens, face unobstructed. These are the best shots the farm has and should always be `strong`.
    2. Sharp, intricate feather / wing-pattern / plumage detail visible — individual feather edges, the pattern of a wing, color gradation on a chick's down. Crisp texture at the pixel level.
    3. A clear portrait of a single identifiable bird (especially Birdadette if she's visible) or a rare-behavior moment (dust-bathing, sparring, mid-stretch, mid-wing-flap, mid-jump, eating something specific, drinking). The subject is obvious and the composition reads as intentional.
  - **"skip" triggers (ALL of these demote the frame regardless of other qualities):**
    1. A group of chicks huddling or sleeping with no visible faces, no individual bird distinguishable, and no unusual posture. A fluffy pile of indistinct chicks is NOT archive-worthy — this is the single most common boring frame.
    2. `activity=none-visible`, empty frame, or no birds in frame.
    3. Compression artifacts, heavy motion blur, subject out of focus beyond recognition.
  - **"decent"** is the middle band: clear, archive-worthy frames that don't hit a `strong` trigger and aren't disqualified by a `skip` trigger. A group shot with multiple visible faces but no clear portrait subject is `decent`. A sharp foraging shot where the bird's face is sideways is `decent`. Normal in-focus frames of birds doing ordinary things are `decent`.
  - If in doubt between `decent` and `skip`, lean `skip`. We'd rather lose a decent frame than drown the gallery in huddle-pile noise.
- `caption_draft`: one sentence, plain language, no proper names, no attribution to real artists or writers. Short and factual.
- `concerns`: only populate if you see something a chicken-keeper should know about — injured bird, dead bird, abnormal posture, fighting/bullying, escape risk, predator visible, environmental hazard. Empty array otherwise.
