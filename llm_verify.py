# Author: Claude Opus 5 (v2.53.0 — local-only rewrite),
#         Claude Sonnet 4.6 (Bubba) (v2.52.1 original, remote OpenAI/OpenRouter)
# Date: 25-July-2026
# PURPOSE: Second-opinion verifier for borderline YOLO predator detections (gate ③ of
#          docs/25-Jul-2026-night-alert-artifact-suppression-plan.md). Asks the vision model
#          already loaded in LM Studio on this Mac Mini whether a detection is a real animal
#          or person, or a camera artifact — and therefore whether it is worth waking Boss.
#
#          *** LOCAL ONLY. THIS MODULE MUST NEVER REACH THE PUBLIC INTERNET. ***
#          v2.52.1 put a metered vision API (OpenRouter -> openai/gpt-4o-mini) on the predator
#          alert path. It made 3,813 calls in two nights, ran the account balance to zero at
#          00:02:20 on 25-Jul-2026, then returned 402 Payment Required 1,147 times in a row.
#          Because verification is fail-open, the alarm silently degraded to "alert on
#          everything" and posted 139 Discord alerts overnight — 135 of them spider webs on the
#          duo2 lens. There is deliberately NO api_key_env, NO api_base override and NO remote
#          fallback in this file. If a future agent is shopping for a hosted vision endpoint
#          from inside this repo, they have already taken a wrong turn: qwen/qwen3-vl-4b is
#          loaded on localhost:1234, costs nothing, and answers in ~1.2s (measured).
#
#          LM Studio safety rules (docs/13-Apr-2026-lm-studio-reference.md, and the 2026-04-13
#          incident that took the whole machine down) are enforced here:
#            - the loaded-model check runs before EVERY call, reusing
#              tools.pipeline.vlm_enricher.list_loaded_models rather than a second copy;
#            - Guardian NEVER loads a model. No /api/v1/models/load, ever. The pipeline's
#              ensure_model_loaded() at daemon startup remains this repo's only load path.
#              A model that is not loaded returns "unavailable", never an auto-load;
#            - a module-level lock keeps Guardian to one in-flight request, mirroring the
#              pipeline's _VLM_LOCK, because both processes share one LM Studio.
#
#          What gets sent: the ANNOTATED FULL FRAME (detection bbox drawn in red), downscaled
#          to 768px long edge — not a bare crop. Measured 25-Jul-2026 on real frames: the crop
#          path took 5,733ms and returned an unexplained "NO"; the annotated full frame took
#          1,434ms and correctly identified "a bright, out-of-focus streak from an insect or
#          spider web on the lens". Cropping to the blob discards the context — position in
#          frame, focus relative to the scene, ground plane — that makes the call decidable.
#
#          Fail-open is preserved but is now GRADUATED, and that policy lives in the caller:
#          this module reports availability honestly via VerificationResult.available and never
#          decides on its own to drop a detection it could not see.
# SRP/DRY check: Pass — single responsibility is one VLM round-trip and its verdict. Reuses
#                vlm_enricher.list_loaded_models (loaded-model check) and mirrors its
#                response_format grammar-sampling pattern rather than reimplementing either.

import base64
import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
import requests

from tools.pipeline.vlm_enricher import list_loaded_models

log = logging.getLogger("guardian.llm_verify")

# One in-flight verification per Guardian process. LM Studio is shared with the image
# pipeline; neither side may fan out. See module header.
_VERIFY_LOCK = threading.Lock()

# Long edge the frame is downscaled to before encoding. Matches the pipeline's
# vlm_input_long_edge_px so both consumers present the model the same scale of image.
_LONG_EDGE_PX = 768

# The model must answer in this shape — enforced server-side by LM Studio's json_schema
# grammar sampling, so it cannot emit anything else.
_VERDICT_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["real", "artifact", "unsure"]},
        "what_it_is": {"type": "string"},
        "alert_worthy": {"type": "boolean"},
    },
    "required": ["verdict", "what_it_is", "alert_worthy"],
    "additionalProperties": False,
}

# Names the real failure modes of these cameras at night. Written from the physically
# confirmed cause (Boss, 25-Jul-2026: fine strands strung from the housing bridge to the lens
# glass) plus the other artifacts this farm actually sees. Note it asks what the thing IS,
# not the leading "is there a person here, YES or NO?" the previous version asked.
_PROMPT = (
    "This is a night infrared frame from a fixed farm security camera. A YOLO detector put the "
    "RED BOX around something it labelled '{class_name}'. Decide whether the red box contains a "
    "real animal or person in the yard, or a camera artifact.\n\n"
    "Common artifacts on these cameras at night: spider webs or insects on the lens lit up by "
    "the infrared illuminator (they appear as bright, blown-out, out-of-focus streaks or bars, "
    "usually at the edge of the frame); IR glare and lens flare; rain or snow streaks; moths "
    "flying close to the lens; static background objects like posts, plants and fence rails.\n\n"
    "A real person or animal has recognisable body shape, limbs, and internal detail, and sits "
    "on the ground plane in the scene. Set alert_worthy true ONLY for a real person or predator "
    "animal."
)


@dataclass
class VerificationResult:
    """Outcome of one verification attempt.

    `available` is the important field: False means the model could not be consulted at all
    (LM Studio down, model not loaded, timeout, transport error). The caller must then apply
    its graduated fail-open policy — this module never silently drops a detection it could
    not actually look at.
    """
    available: bool
    alert_worthy: bool
    verdict: str = "unavailable"
    what_it_is: str = ""
    latency_ms: int = 0
    error: Optional[str] = None

    @property
    def suppressed(self) -> bool:
        """True only when the model looked and said this is not worth alerting on."""
        return self.available and not self.alert_worthy


def _annotate_and_encode(frame: np.ndarray, bbox, long_edge: int = _LONG_EDGE_PX) -> str:
    """Draw the detection box on a copy of the frame, downscale, return base64 JPEG."""
    annotated = frame.copy()
    x1, y1, x2, y2 = (int(v) for v in bbox)
    # Thickness scales with frame size so the box survives the downscale to 768px.
    thickness = max(2, int(round(max(frame.shape[:2]) / 500)))
    cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 0, 255), thickness)

    height, width = annotated.shape[:2]
    scale = long_edge / max(height, width)
    if scale < 1:
        annotated = cv2.resize(
            annotated, (int(width * scale), int(height * scale)),
            interpolation=cv2.INTER_AREA,
        )

    ok, jpeg = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("JPEG encode failed")
    return base64.b64encode(jpeg.tobytes()).decode("ascii")


def verify_detection(
    frame: np.ndarray,
    class_name: str,
    confidence: float,
    bbox,
    lm_base: str = "http://localhost:1234",
    model: str = "qwen/qwen3-vl-4b",
    timeout_s: int = 10,
) -> VerificationResult:
    """Ask the local VLM whether this detection is worth alerting on.

    Returns a VerificationResult. Never raises. An `available=False` result means the caller
    must decide (graduated fail-open); it does NOT mean "suppress".
    """
    try:
        # Loaded-model guard. Guardian is a read-only consumer of LM Studio — if the model
        # is not up we report unavailable and let the caller fail open. We never load it.
        try:
            loaded = list_loaded_models(lm_base, timeout=5)
        except Exception as exc:
            log.warning(
                "LLM verify: cannot reach LM Studio at %s (%s) — reporting unavailable",
                lm_base, exc,
            )
            return VerificationResult(
                available=False, alert_worthy=True, error=f"lm-studio-unreachable: {exc}"
            )

        if model not in loaded:
            log.warning(
                "LLM verify: model %r is not loaded (loaded: %s) — reporting unavailable. "
                "Guardian never auto-loads; the pipeline's ensure_model_loaded() owns that.",
                model, loaded,
            )
            return VerificationResult(
                available=False, alert_worthy=True, error="model-not-loaded"
            )

        b64 = _annotate_and_encode(frame, bbox)
        body = {
            "model": model,
            "temperature": 0,
            "max_tokens": 120,
            # LM Studio's OpenAI-compat switch for thinking models. The native-API
            # `reasoning: "off"` is silently ignored here — see vlm_enricher's note.
            "reasoning_effort": "none",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "detection_verdict",
                    "strict": "true",
                    "schema": _VERDICT_SCHEMA,
                },
            },
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT.format(class_name=class_name)},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }],
        }

        with _VERIFY_LOCK:
            started = time.monotonic()
            response = requests.post(
                f"{lm_base}/v1/chat/completions", json=body, timeout=timeout_s
            )
            latency_ms = int((time.monotonic() - started) * 1000)
        response.raise_for_status()

        import json  # local import: only needed on the success path
        content = response.json()["choices"][0]["message"]["content"]
        parsed = json.loads(content)

        verdict = parsed.get("verdict", "unsure")
        what_it_is = parsed.get("what_it_is", "")
        alert_worthy = bool(parsed.get("alert_worthy", True))

        # "unsure" always resolves toward alerting. Ambiguity wakes Boss; it never
        # produces silence.
        if verdict == "unsure":
            alert_worthy = True

        log.info(
            "LLM verify: %s @ %.2f -> %s (%s) -> %s [%dms]",
            class_name, confidence, verdict, what_it_is or "no description",
            "ALERT" if alert_worthy else "SUPPRESS", latency_ms,
        )
        return VerificationResult(
            available=True, alert_worthy=alert_worthy, verdict=verdict,
            what_it_is=what_it_is, latency_ms=latency_ms,
        )

    except requests.Timeout:
        log.warning(
            "LLM verify: timed out after %ss for %s — reporting unavailable",
            timeout_s, class_name,
        )
        return VerificationResult(available=False, alert_worthy=True, error="timeout")
    except Exception as exc:
        log.warning(
            "LLM verify: error for %s (%s) — reporting unavailable", class_name, exc
        )
        return VerificationResult(available=False, alert_worthy=True, error=str(exc))
