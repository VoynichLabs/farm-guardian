# Provenance text for the S7 reaction dataset (Kaggle "Provenance" boxes)

Author: Claude Sonnet 5.5 Medium. Date: 07-Nov-2026.

The two sections below are the single source for the Provenance boxes on the Kaggle dataset page
(Sources, Collection Methodology). `scripts/kaggle-s7-package.py` reads them, writes the Sources text
into the metadata update, and writes both texts to `/Volumes/Team 4TB/kaggle/s7-reaction-20261105/kaggle-meta/`
(the Kaggle command line has no field for Collection Methodology, so that one is pasted into the page).
Plain text only: no placeholders, no personal names, no location beyond "a small farm".
Counts that change with the data are left out here on purpose and live in the card.

## Sources

Photographs: a Samsung Galaxy S7 phone camera at a fixed spot over the poultry enclosure of my small farm (chickens and turkeys), archived by the Farm Guardian project (VoynichLabs). Captions and the other descriptive columns and sub-scores: written by small vision-language models run on the farm's own computer (almost all by Qwen3-VL 4B, a few by larger Qwen models that were loaded at the time). Bird coverage ("measured_fill_pct"): measured by a YOLOv8s object detector. Reactions: emoji reactions (any emoji) from non-bot accounts on the farm's Discord channel, where the best frames were posted, counted by the project's reaction-sync job. The labelled pictures were selected and joined by the dataset's author; see Collection Methodology. The same frames also feed my Instagram Reels. Later data will be judged by Gemma 4 E2B instead of Qwen.

## Collection methodology

1. Capture. A phone camera in a fixed position over the poultry enclosure (chickens and turkeys) takes a picture every few seconds, daylight only. The phone was replaced by another Galaxy S7 on 10 August 2026. In the first weeks some frames come from other spots and the phone was sometimes carried indoors.

2. Scoring and posting. A small vision-language model reads each frame and writes a caption, a bird count, a scene and activity label and sub-scores. The farm's pipeline then computes the overall 0 to 100 score from those sub-scores and a fill term (from 2 October a pixel-based sharpness check can also overrule the model's sharpness call). Only frames the model rated "strong", that cleared a minimum score and passed a few further checks were posted, to a Discord channel, with the picture, the caption and the score. The scoring rules changed several times (see the scoring eras in the dataset card), so the same score means different things in different eras.

3. Labels. People in that Discord server react to posts with any emoji; the emoji is not recorded, so a reaction means someone reacted, not necessarily that they liked it. Most reactions are mine, but anyone in my server can react. A reaction-sync job counts, for each post, how many different non-bot people reacted (reaction_people) and stamps the post's message id on the archived frame. reacted is 1 when that count is at least one. Who reacted is not kept. A zero means no reaction was recorded, not that anyone disliked the picture.

4. Joining. Reacted posts are tied to their frame by the stamped message id. Unreacted posts are tied by capture time (one minute before the post) and score, with a caption check when several frames fit. Posts that could not be tied to a single frame are left out, as are the most recent posts, which have not had time to collect a reaction.

5. Packaging. One row per frame, original 1080 by 1920 pictures with no metadata, a suggested split by whole calendar days, and columns that would give the label away removed. The captions, vision-model columns and scores are model output and are often wrong (see the card). Pictures were not hand-checked. The only person who appears in them is me.

6. Caption spot check. 160 frames, 40 per scoring era, were drawn at random and checked twice by AI readers (Claude models from Anthropic): once comparing the caption with the picture, and once blind, classifying which way the main bird faces. Disagreements were settled by a third reader. This is an informal check by AI readers, not a human audit.
