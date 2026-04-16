# 08 — silent_control

The pure-silence baseline trial. No audio is played; the runner just marks T0 in the trials log and captures the same baseline + response + tail frame burst as a real-stimulus trial.

The runner can either ship a 5 s zero-amplitude WAV here (for parity in the playback path) or skip the playback call entirely. Both are valid. If we ship a WAV, it stays in this directory as `00-silent.wav` so the MANIFEST has a row for it and the category isn't a special case in the schema.
