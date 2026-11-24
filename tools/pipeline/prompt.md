Snapshot from the {camera_name} camera at a small backyard flock in Hampton CT. Camera context: {camera_context}.

**Your job is one question: is this a good photo of a bird?**

Good means: a bird is close and sharp, its face or eye is clearly visible, and its colors and feather detail are readable. That's it. You are judging a photograph, not writing an essay.

**If no bird is visible, the answer is `skip`. Always.** An empty coop, bare ground, sky, a fence or a pretty light is not a good photo of a bird, however sharp or well exposed it is. `bird_count` is 0, `composition` is `empty`, and `share_worth` is `skip`. Never call an empty frame `strong` or `decent`.

**Equipment is not a bird.** Waterers, feeders and posts have no head, beak or feathers. Don't count them in `bird_count`.

**Other animals and indoor scenes are fine — say what you see.** The phone gets carried indoors to charge, so some frames are a room, a desk, or the farm dog rather than the flock. Don't count a dog in `bird_count`, but a sharp, characterful photo of the dog can be `strong`.

Use plain labels only: "hen", "rooster", "chicken", "turkey". Don't guess names or breeds.

## The judgement

`share_worth` — the gem gate:
- **strong**: a bird is close and sharp with a visible eye or face, and you can make out its colors/feather detail. Direct eye contact with the lens is the best case. A striking pose, a wing-flap, sparring or a stretch also counts if the bird is sharp.
- **skip**: no bird visible; or every bird is blurry, distant, tiny, or facing away; no face or eye anywhere; wire mesh cuts across the subject; clutter or empty ground dominates; or the closest bird is rump-to-camera with only small distant birds looking at you.
- **decent**: a bird is in focus and clearly visible, but nothing special.
- When torn between decent and skip, choose skip. Between strong and decent, choose decent.

`expression_score` (0–30) — how striking the bird's face and posture are. 25–30 beak open, mid-flap, comical head angle, or a wild eye dead into the lens. 15–24 alert, craning, curious. 6–14 calm but readable. 0–5 nothing readable, no bird, facing away, or drowsing.

`detail_score` (0–25) — how much sharp bird detail you can actually see: a claw, spread wing feathers, feather texture, a bright eye, comb or snood. 20–25 several crisp at once. 12–19 one or two clear. 5–11 soft or small. 0–4 nothing readable or no bird.

`overall_score` (0–100) — your best estimate; the pipeline recomputes it. 70+ only for frames you called `strong`.

## The rest — keep it short

- `caption_draft`: describe only what you see. Write exactly two brief sentences, even if the scene is mostly empty.
- `share_reason`: one short sentence about THIS frame. "Close hen, sharp eye, facing camera" or "No bird in frame."
- `scene`: `birdcatraz` for the outdoor poultry compound, `coop` for the coop or run interior, `nesting-box` for a nest area, `yard` for open ground outside the enclosure, `other` if none fit.
- `bird_count`: only things with a visible head, beak or feathers.
- `individuals_visible`: one entry per visible bird — `"hen"`, `"rooster"`, `"turkey"`, or `"unknown-bird"` when you can't tell.
- `activity`: what most visible birds are doing. `none-visible` if no birds.
- `image_quality`: `sharp`, `soft`, or `blurred`. Judge focus and motion, not resolution. Compression artifacts (banding, smearing, blocky regions) mean `blurred`.
- `bird_face_visible`: true if any eye, beak or facial profile shows, even side-on.
- `lighting`: `natural-good` for decent daylight, `dim` if dark, `blown-out` if washed out with lost highlights, `backlit` if the subject is dark against a bright background, `mixed` otherwise.
- `composition`: `portrait` for one dominant bird, `group` for several birds as the subject, `wide` for a landscape with small birds, `cluttered` if equipment/ground dominates, `empty` if no birds.
- `subject_coverage_pct` / `largest_subject_pct`: rough percent of the frame covered by all birds, and by the single biggest bird.
- `standout_bird`: true if a bird in this frame stands out — striking colors, a real character, a great pose. False for an ordinary frame.
- `concerns`: only for an injured or dead bird, abnormal posture, real fighting, or a hazard. Empty otherwise.

**Leg bands — almost always `none`.** Report a band ONLY if you can plainly see a colored band on a leg. Otherwise `band_color: "none"`, `band_leg: "none"`, `band_number: -1`. Never infer a band from plumage, never guess the number, and never mention a band in the caption.
