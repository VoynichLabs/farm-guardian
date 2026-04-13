This is a snapshot from the {camera_name} camera at a small backyard flock in Hampton CT. Camera context: {camera_context}.

Known birds in this flock as of {today}:
- Birdadette: 3rd-gen Speckled Sussex hen, ~1 yo, GPU-incubated
- Four winter-survivor adults (mixed breeds — not individually distinct in most photos)
- ~22 brooder chicks, currently 1-3 weeks old, mixed exotic-island-fowl variants and rare breeds. Most are visually similar; flag anything that stands out from its siblings.

Output ONLY a single JSON object that conforms to the provided schema. No prose, no commentary, no markdown fences. The system will reject your response if it does not parse as valid JSON matching the schema.

Guidance on specific fields:
- `scene`: pick the best match for what's visible; "other" only if nothing else fits.
- `bird_count`: your best estimate; 0 if none visible.
- `individuals_visible`: only include what you can actually see. "birdadette" only if you see a Speckled Sussex hen (white-speckled on dark base, medium-large). "chick" for any bird that looks like a chick (fluffy, small, brooder-aged). "adult-survivor" for any non-Birdadette mature bird. "unknown-bird" for edge cases.
- `any_special_chick`: true only if one chick is visually distinct from the others in the frame (unusual coloring, markedly different size, unusual posture).
- `apparent_age_days`: -1 for non-chick scenes (this means "not applicable"). For chicks, your best guess 1-60.
- `activity`: what the majority of visible birds are doing. "none-visible" if no birds in frame.
- `image_quality`: "sharp" if fine details readable, "soft" if details hazy but subjects identifiable, "blurred" if subjects not identifiable.
- `share_worth`: "strong" only for frames genuinely interesting or beautiful enough to post publicly. "decent" for clear, normal, archive-worthy frames. "skip" for boring, blurry, or empty frames.
- `caption_draft`: one sentence, plain language, no proper names, no attribution to real artists or writers. Short and factual.
- `concerns`: only populate if you see something a chicken-keeper should know about — injured bird, dead bird, abnormal posture, fighting/bullying, escape risk, predator visible, environmental hazard. Empty array otherwise.
