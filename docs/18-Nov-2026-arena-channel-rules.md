<!--
Author: Claude Sonnet 5.5 Low
Date: 18-Nov-2026
PURPOSE: The exact text posted (and pinned, if the bot may) in the Autoresearch Arena #farm
         channel (1572675202096173146), explaining what the S7 pictures are, what score gets a
         picture posted, what each reaction means, and that silence counts as "scored too high".
         Single source for that text. Re-post / edit the pinned message when the posting gate in
         tools/pipeline/gem_poster.py changes (the score floor, sharpness and face rules, subject
         fill), when the vocabulary in scripts/arena-label-sync.py changes, or when the vision model
         changes.
SRP/DRY check: Pass — the numbers here are copied from gem_poster._MIN_OVERALL_SCORE,
               _LARGEST_SUBJECT_PCT_MIN and the BIRD SELFIE ping score in orchestrator; they are not
               restated anywhere else for people to read.
-->

# #farm channel rules (message text)

**What you're looking at.** Every picture here comes from the S7 camera at Birdcatraz. A vision model running locally on the Mac Mini looks at the camera's frames all day, writes the caption and rates the picture. The computer then turns those ratings, how much of the frame the birds fill and how many birds there are into a score out of 100 (the ⭐ line under each picture).

**What gets posted.** Only a picture that scores **82 or higher**, is rated strong, is judged sharp (a separate check on the pixels can overrule that if part of the frame is out of focus), and has a bird's face visible, even from the side. The birds also have to fill a good part of the frame: in practice about a third of the picture or more. A **BIRD SELFIE 💯** ping means the score was 87 or higher. There is no limit on how many can post, so when a bird sits still you may get several almost-identical pictures in a row.

**Tell us what you think. React:**
❤️ I like it
💯 It deserves a higher score (counts as a like too)
❌ Something about it is wrong (the picture or the caption)
🧐 Questionable, look closer
Use Reply on a picture to say why. A reply counts as someone having looked at it.

**Silence counts.** If a day goes by while people are around and nobody reacts or replies to a picture, we record that it was scored too high.

All of this becomes labeled data: which pictures people like, and which captions are wrong. It may turn into a Kaggle competition about predicting chicken pictures.
