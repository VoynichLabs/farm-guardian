# 12-Oct-2026 — Bird-pipeline VLM throughput: what the ARC-2 speed work suggests

Author: Claude Opus 5.5 ("Dr. Opus", the ARC-2 speed session), at the Boss's request: "in the bird pipeline VLM,
it's all about maximum tokens per second." This is a note, not a plan. Nothing in Farm Guardian was changed.

## What one frame costs today (measured)

From LM Studio's server log, 12-Oct 15:00-15:51 local, `qwen/qwen3-vl-4b` (lmstudio-community
`Qwen3-VL-4B-Instruct-Q4_K_M.gguf` + F16 mmproj, llama.cpp engine, Mac Mini M4 Pro):

| Step | Tokens | Speed | Time |
|---|---|---|---|
| Read the prompt (768 px image + text) | ~342 | ~385 tok/s | ~0.9 s |
| Write the JSON answer | ~200-220 | ~36 tok/s | ~5.5-6 s |

About 85% of each frame's time goes to writing the answer, one token at a time. Calls are serial: the main
scheduler sends one frame at a time. That hour logged ~114 requests, about 2 a minute.

## Would the ARC-2 model be faster? No.

The ARC-2 model is Qwen3.8-27B, about 7x the size of the 4B. On this Mac it would be several times *slower* per
frame. It has sat loaded here before, on 8-Sep and 11-Oct. Farm Guardian then either skipped frames or used it in
place of the 4B.

Its speed-up on Kaggle came from two things:
1. **A draft model** (DFlash2) guesses several tokens ahead, and the big model checks them in one pass. This needs
   vLLM on NVIDIA GPUs and a drafter trained for that exact model, so it doesn't carry over to LM Studio and
   Qwen3-VL-4B.
2. **Many requests at once ("lanes").** This carries over directly.

## Levers, biggest first

1. **Write fewer tokens per frame.** Time scales with the ~210 output tokens:
   - Drop JSON fields nothing downstream reads.
   - Shorten free-text fields.
   - Or split the call. A short gate first: a few tokens saying whether there's a postable bird. Then ask for the
     full description only for keepers.

   If most frames are rejects, this is a several-fold gain in frames an hour, with no new hardware.
2. **Judge several frames at once.**
   - Load the model with parallel slots (LM Studio "max concurrent predictions", or `lms load qwen/qwen3-vl-4b
     --parallel 4`), and let the scheduler send 2-4 frames concurrently. The current VLM-serialized scheduler
     would need changing.
   - Single-stream decode on Apple Silicon is limited by memory bandwidth. A batch of 4 costs little more per step
     than 1, so total tokens a second typically rises 2-3x, with each request a little slower.
   - This is the lanes lesson from ARC-2. Measure it here before relying on it.
3. **Re-measure on a quiet machine first.**
   - 36 tok/s is about half what an M4 Pro should manage for a 2.3 GB 4-bit model.
   - During this measurement the Mac was busy: other sessions' CPU-heavy jobs, Spotlight indexing, and a 16 GB
     Qwen 27B loaded in LM Studio by an idle ARC-2 loop. Dr. Opus stopped that loop and unloaded the 27B,
     12-Oct 19:50 UTC.
4. **Try the MLX build of Qwen3-VL-4B (4-bit)** in LM Studio. On Apple Silicon, MLX often decodes faster than
   GGUF. A/B it on the same frames.
5. **Speculative decoding** (a draft model) in LM Studio works for text models. Support for vision models is
   uncertain, and the gain shrinks once requests are batched. Lowest priority.

## How to measure

LM Studio's server log (`~/.lmstudio/server-logs/YYYY-MM/`) prints each request's `prompt eval time` and
`eval time` (tokens and tok/s). Compare before and after on the same set of frames. The number that matters is
frames judged an hour at unchanged accuracy, not tokens a second alone.
