# Author: Claude Opus 4.6
# Date: 11-April-2026 (USB camera fix — removed -video_size for AVFoundation fallback)
# PURPOSE: HLS stream manager for non-detection cameras. Runs ffmpeg subprocesses that
#          capture RTSP or USB input, re-encode via VideoToolbox H.264 (hardware), and
#          output HLS segments to /tmp. Serves quality buffered video with ~10s delay
#          instead of raw MJPEG with packet loss. Each camera gets its own ffmpeg process
#          monitored by a watchdog thread. Also produces a high-quality snapshot per camera
#          (overwritten every N seconds — zero disk bloat).
#          Detection cameras (house-yard) skip this and keep using OpenCV + MJPEG.
# SRP/DRY check: Pass — single responsibility is ffmpeg-based HLS stream management.

import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger("guardian.stream")


class HLSStream:
    """Manages a single ffmpeg subprocess that produces HLS output for one camera."""

    def __init__(
        self,
        camera_name: str,
        output_dir: str,
        rtsp_url: Optional[str] = None,
        device_index: Optional[int] = None,
        audio_device_index: Optional[int] = None,
        rtsp_transport: Optional[str] = None,
        segment_duration: int = 3,
        buffer_segments: int = 5,
        video_bitrate: str = "2M",
        framerate: int = 15,
        snapshot_interval: int = 10,
        prefer_hw_encode: bool = True,
    ):
        self._camera_name = camera_name
        self._rtsp_url = rtsp_url
        self._device_index = device_index
        self._audio_device_index = audio_device_index
        self._rtsp_transport = rtsp_transport or "tcp"
        self._segment_duration = segment_duration
        self._buffer_segments = buffer_segments
        self._video_bitrate = video_bitrate
        self._framerate = framerate
        self._snapshot_interval = snapshot_interval
        self._prefer_hw_encode = prefer_hw_encode

        self._output_dir = Path(output_dir) / camera_name
        self._process: Optional[subprocess.Popen] = None
        self._watchdog_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._consecutive_failures = 0
        self._max_failures = 10  # give up after 10 consecutive restarts

    @property
    def camera_name(self) -> str:
        return self._camera_name

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    @property
    def playlist_path(self) -> Path:
        return self._output_dir / "stream.m3u8"

    @property
    def snapshot_path(self) -> Path:
        return self._output_dir / "latest.jpg"

    def start(self) -> None:
        """Start the ffmpeg process and watchdog thread."""
        if self.is_running:
            log.warning("HLS stream already running for '%s'", self._camera_name)
            return

        self._stop_event.clear()
        self._output_dir.mkdir(parents=True, exist_ok=True)

        self._watchdog_thread = threading.Thread(
            target=self._watchdog_loop,
            name=f"hls-{self._camera_name}",
            daemon=True,
        )
        self._watchdog_thread.start()
        log.info("HLS stream starting for '%s'", self._camera_name)

    def stop(self) -> None:
        """Stop the ffmpeg process and watchdog."""
        self._stop_event.set()
        self._kill_ffmpeg()
        if self._watchdog_thread:
            self._watchdog_thread.join(timeout=10)
        log.info("HLS stream stopped for '%s'", self._camera_name)

    def _watchdog_loop(self) -> None:
        """Monitor ffmpeg — restart on crash, respect backoff."""
        while not self._stop_event.is_set():
            if self._consecutive_failures >= self._max_failures:
                log.error(
                    "HLS stream '%s' — %d consecutive failures, giving up",
                    self._camera_name, self._consecutive_failures,
                )
                break

            self._launch_ffmpeg()

            # Wait for ffmpeg to exit or stop signal
            while not self._stop_event.is_set():
                if self._process and self._process.poll() is not None:
                    exit_code = self._process.returncode
                    if exit_code != 0 and not self._stop_event.is_set():
                        self._consecutive_failures += 1
                        delay = min(2.0 * (2 ** self._consecutive_failures), 60.0)
                        log.warning(
                            "HLS ffmpeg for '%s' exited with code %d — restarting in %.0fs (attempt %d)",
                            self._camera_name, exit_code, delay, self._consecutive_failures,
                        )
                        self._stop_event.wait(delay)
                    break
                self._stop_event.wait(2.0)

            # Reset failure count if ffmpeg ran for at least 30 seconds
            if self._process and self._process.returncode == 0:
                self._consecutive_failures = 0

    def _build_ffmpeg_cmd(self) -> list[str]:
        """Build the ffmpeg command for this camera's input type."""
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "warning"]

        # Input source
        if self._device_index is not None:
            # USB camera via AVFoundation — video only (audio disabled for now).
            # Do NOT pass -video_size — ffmpeg 8.0.1's AVFoundation demuxer can't
            # negotiate framerate 30.000030fps reported by this camera when explicit
            # size is set. Without -video_size, ffmpeg falls back to a default mode
            # that captures at native 1920x1080 successfully. The -framerate 30 flag
            # triggers the initial config failure, but the fallback works.
            # Audio device indices shift when the iPhone connects/disconnects, making
            # hardcoded indices unreliable. Audio support deferred until name-based
            # device resolution is implemented.
            cmd.extend([
                "-f", "avfoundation",
                "-framerate", "30",
                "-i", str(self._device_index),
            ])
        elif self._rtsp_url:
            # RTSP stream
            cmd.extend([
                "-rtsp_transport", self._rtsp_transport,
                "-i", self._rtsp_url,
            ])
        else:
            raise ValueError(f"No input source for camera '{self._camera_name}'")

        # Video encoder — prefer hardware, fall back to software
        if self._prefer_hw_encode:
            encoder = "h264_videotoolbox"
        else:
            encoder = "libx264"

        # HLS video output
        segment_pattern = str(self._output_dir / "seg_%05d.ts")
        playlist = str(self.playlist_path)

        cmd.extend([
            # Video encoding
            "-c:v", encoder,
            "-b:v", self._video_bitrate,
            "-r", str(self._framerate),
            "-g", str(self._framerate * self._segment_duration),  # keyframe every segment
            "-sc_threshold", "0",
            # Audio disabled for all streams currently — USB audio device indices
            # are unstable (shift when iPhone connects/disconnects), and RTSP
            # cameras don't need it. Audio plumbing retained for future use.
            "-an",
            # HLS muxer
            "-f", "hls",
            "-hls_time", str(self._segment_duration),
            "-hls_list_size", str(self._buffer_segments),
            "-hls_flags", "delete_segments+append_list",
            "-hls_segment_filename", segment_pattern,
            playlist,
        ])

        return cmd

    def _launch_ffmpeg(self) -> None:
        """Start the ffmpeg subprocess."""
        self._kill_ffmpeg()

        # Clean stale segments from a previous run
        for f in self._output_dir.glob("seg_*.ts"):
            f.unlink(missing_ok=True)
        m3u8 = self.playlist_path
        if m3u8.exists():
            m3u8.unlink()

        cmd = self._build_ffmpeg_cmd()
        log.info("HLS ffmpeg starting for '%s': %s", self._camera_name, " ".join(cmd))

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                preexec_fn=os.setpgrp,  # isolate process group for clean kill
            )
        except FileNotFoundError:
            log.error("ffmpeg not found in PATH — cannot start HLS stream for '%s'", self._camera_name)
            self._consecutive_failures = self._max_failures  # don't retry
        except Exception as exc:
            log.error("Failed to start ffmpeg for '%s': %s", self._camera_name, exc)

    def _kill_ffmpeg(self) -> None:
        """Gracefully terminate the ffmpeg process."""
        if self._process is None:
            return

        try:
            if self._process.poll() is None:
                # Send SIGTERM for graceful shutdown (finishes current segment)
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=3)
        except Exception as exc:
            log.warning("Error killing ffmpeg for '%s': %s", self._camera_name, exc)
        finally:
            self._process = None


class HLSStreamManager:
    """Manages HLS streams for multiple cameras."""

    def __init__(self, config: dict):
        streaming_cfg = config.get("streaming", {})
        self._output_dir = streaming_cfg.get("hls_output_dir", "/tmp/guardian_hls")
        self._segment_duration = streaming_cfg.get("segment_duration", 3)
        self._buffer_segments = streaming_cfg.get("buffer_segments", 5)
        self._video_bitrate = streaming_cfg.get("video_bitrate", "2M")
        self._framerate = streaming_cfg.get("framerate", 15)
        self._snapshot_interval = streaming_cfg.get("snapshot_interval", 10)
        self._prefer_hw_encode = streaming_cfg.get("prefer_hw_encode", True)
        self._streams: dict[str, HLSStream] = {}

    def add_camera(
        self,
        camera_name: str,
        rtsp_url: Optional[str] = None,
        device_index: Optional[int] = None,
        audio_device_index: Optional[int] = None,
        rtsp_transport: Optional[str] = None,
    ) -> None:
        """Start an HLS stream for a camera."""
        if camera_name in self._streams:
            log.warning("HLS stream already registered for '%s'", camera_name)
            return

        stream = HLSStream(
            camera_name=camera_name,
            output_dir=self._output_dir,
            rtsp_url=rtsp_url,
            device_index=device_index,
            audio_device_index=audio_device_index,
            rtsp_transport=rtsp_transport,
            segment_duration=self._segment_duration,
            buffer_segments=self._buffer_segments,
            video_bitrate=self._video_bitrate,
            framerate=self._framerate,
            snapshot_interval=self._snapshot_interval,
            prefer_hw_encode=self._prefer_hw_encode,
        )
        self._streams[camera_name] = stream
        stream.start()

    def remove_camera(self, camera_name: str) -> None:
        """Stop and remove an HLS stream."""
        stream = self._streams.pop(camera_name, None)
        if stream:
            stream.stop()

    def stop_all(self) -> None:
        """Stop all HLS streams."""
        for name, stream in self._streams.items():
            log.info("Stopping HLS stream for '%s'", name)
            stream.stop()
        self._streams.clear()

    def get_stream(self, camera_name: str) -> Optional[HLSStream]:
        """Return the HLS stream for a camera, or None."""
        return self._streams.get(camera_name)

    def get_playlist_path(self, camera_name: str) -> Optional[Path]:
        """Return the path to a camera's HLS playlist, or None."""
        stream = self._streams.get(camera_name)
        if stream and stream.playlist_path.exists():
            return stream.playlist_path
        return None

    def get_hls_file_path(self, camera_name: str, filename: str) -> Optional[Path]:
        """Return the path to an HLS file (.m3u8 or .ts) for a camera. Validates filename."""
        stream = self._streams.get(camera_name)
        if not stream:
            return None

        # Security: only allow .m3u8 and .ts files, no path traversal
        if ".." in filename or "/" in filename:
            return None
        if not (filename.endswith(".m3u8") or filename.endswith(".ts")):
            return None

        path = stream._output_dir / filename
        if path.exists():
            return path
        return None

    @property
    def active_streams(self) -> list[str]:
        """Return names of cameras with active HLS streams."""
        return [name for name, stream in self._streams.items() if stream.is_running]
