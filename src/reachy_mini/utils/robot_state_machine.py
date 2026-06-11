"""Robot State Machine — Central coordination of voice, LLM, motion, error and eye states.

Designed for modular expansion: camera vision, emotion recognition, posture detection,
more expressions, motion sync and lip-sync can be plugged in later.

Author: Reachy Mini Eye Integration
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional

# Lazy import to avoid circular dependency at import time
from reachy_mini.io.eye_controller import EyeController, EyeState


# ---------------------------------------------------------------------------
# Sub-state enums
# ---------------------------------------------------------------------------
class VoiceState(Enum):
    """Voice / TTS subsystem states."""

    IDLE = auto()
    LISTENING = auto()       # Wake-word detected, recording audio
    RECOGNIZING = auto()     # Speech-to-text in progress
    SPEAKING = auto()        # TTS output active


class LLMState(Enum):
    """LLM inference subsystem states."""

    IDLE = auto()
    PROMPTING = auto()       # Building the prompt
    GENERATING = auto()      # Tokens streaming
    POSTPROCESSING = auto()  # Post-generation formatting


class MotionState(Enum):
    """Motion / actuator subsystem states."""

    IDLE = auto()
    PREPARING = auto()       # Trajectory planning
    EXECUTING = auto()       # Servos moving
    HOLDING = auto()         # Holding position


class ErrorState(Enum):
    """Error / health monitoring subsystem states."""

    NONE = auto()
    WARNING = auto()         # Degraded but operational
    CRITICAL = auto()        # Stop motion, lock eyes to ERROR


# ---------------------------------------------------------------------------
# EyeState is already defined in eye_controller.py
# Mapping: subsystem states -> eye expression
# ---------------------------------------------------------------------------
_EYE_MAP: Dict[Any, EyeState] = {
    # VoiceState
    VoiceState.IDLE: EyeState.AWAIT,
    VoiceState.LISTENING: EyeState.LISTENING,
    VoiceState.RECOGNIZING: EyeState.THINKING,
    VoiceState.SPEAKING: EyeState.RESPONSE,
    # LLMState
    LLMState.IDLE: EyeState.NORMAL,
    LLMState.PROMPTING: EyeState.THINKING,
    LLMState.GENERATING: EyeState.THINKING,
    LLMState.POSTPROCESSING: EyeState.THINKING,
    # MotionState
    MotionState.IDLE: EyeState.NORMAL,
    MotionState.PREPARING: EyeState.NORMAL,
    MotionState.EXECUTING: EyeState.AWAKEN,
    MotionState.HOLDING: EyeState.NORMAL,
}


# ---------------------------------------------------------------------------
# Event dataclass for extensibility
# ---------------------------------------------------------------------------
@dataclass
class StateEvent:
    """A state-change event that can carry arbitrary payload for future extensions."""

    subsystem: str                          # "voice", "llm", "motion", "error", "eye"
    old_state: Any
    new_state: Any
    timestamp: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Main State Machine
# ---------------------------------------------------------------------------
class RobotStateMachine:
    """Central robot state machine coordinating all subsystems.

    Args:
        eye_controller: An EyeController instance (optional — eyes disabled if None).
        on_state_change: Optional callback ``fn(event: StateEvent)`` for external logging / telemetry.
        on_play_audio: Optional callback ``fn(audio_path: str)`` to play audio files.
            Used for wake-word greeting (e.g. "你好，我在").
    """

    def __init__(
        self,
        eye_controller: Optional[EyeController] = None,
        on_state_change: Optional[Callable[[StateEvent], None]] = None,
        on_play_audio: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.logger = logging.getLogger(__name__)
        self._eye = eye_controller
        self._on_state_change = on_state_change
        self._on_play_audio = on_play_audio

        # Subsystem states
        self._voice = VoiceState.IDLE
        self._llm = LLMState.IDLE
        self._motion = MotionState.IDLE
        self._error = ErrorState.NONE

        # Synchronisation
        self._lock = threading.RLock()
        self._running = False
        self._worker: Optional[threading.Thread] = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Start the state-machine background sync loop."""
        if self._running:
            return
        self._running = True
        self._worker = threading.Thread(target=self._sync_loop, daemon=True)
        self._worker.start()
        self.logger.info("RobotStateMachine started.")

    def stop(self) -> None:
        """Stop the background loop."""
        self._running = False
        if self._worker is not None:
            self._worker.join(timeout=2.0)
            self._worker = None
        self.logger.info("RobotStateMachine stopped.")

    # -- public query API ----------------------------------------------------

    @property
    def voice_state(self) -> VoiceState:
        with self._lock:
            return self._voice

    @property
    def llm_state(self) -> LLMState:
        with self._lock:
            return self._llm

    @property
    def motion_state(self) -> MotionState:
        with self._lock:
            return self._motion

    @property
    def error_state(self) -> ErrorState:
        with self._lock:
            return self._error

    # -- wake-word & greeting ------------------------------------------------

    def on_wake_word_detected(self, greeting_audio: Optional[str] = None) -> None:
        """Wake-word "墩墩" detected → AWAKEN briefly, then LISTENING.

        This is NOT the RESPONSE state. The robot simply acknowledges being
        called ("你好，我在") and then enters LISTENING to wait for the
        user's actual question.

        Args:
            greeting_audio: Path to the greeting audio file (e.g. "nihao_wozai.wav").
                If provided and ``on_play_audio`` callback is set, it will be played.
        """
        self._transition("voice", self._voice, VoiceState.LISTENING)
        self._set_eye(EyeState.AWAKEN)

        # Play greeting if callback available
        if greeting_audio and self._on_play_audio:
            try:
                self._on_play_audio(greeting_audio)
            except Exception:
                self.logger.exception("Failed to play greeting audio")

        # After 2 s auto-switch to LISTENING eye (unless overridden)
        def _delayed_listening() -> None:
            # Only switch if we're still in the wake-word flow
            with self._lock:
                if self._voice == VoiceState.LISTENING and self._llm == LLMState.IDLE:
                    self._set_eye(EyeState.LISTENING)

        threading.Timer(2.0, _delayed_listening).start()

    # -- voice callbacks -----------------------------------------------------

    def on_listening_start(self) -> None:
        """User is speaking (audio streaming to STT)."""
        self._transition("voice", self._voice, VoiceState.LISTENING)
        self._set_eye(EyeState.LISTENING)

    def on_listening_end(self) -> None:
        """Audio capture finished → STT processing."""
        self._transition("voice", self._voice, VoiceState.RECOGNIZING)
        self._set_eye(EyeState.THINKING)

    def on_tts_start(self) -> None:
        """TTS playback begins — this IS a formal RESPONSE."""
        self._transition("voice", self._voice, VoiceState.SPEAKING)
        self._set_eye(EyeState.RESPONSE)

    def on_tts_end(self) -> None:
        """TTS playback finished."""
        self._transition("voice", self._voice, VoiceState.IDLE)
        self._resolve_eye()

    # -- LLM callbacks -------------------------------------------------------

    def on_llm_start(self) -> None:
        """LLM generation begins."""
        self._transition("llm", self._llm, LLMState.GENERATING)
        self._set_eye(EyeState.THINKING)

    def on_llm_token(self, token: str) -> None:
        """Optional: called on every streamed token (for future lip-sync)."""
        # Placeholder for future lip-sync integration
        pass

    def on_llm_end(self) -> None:
        """LLM generation finished."""
        self._transition("llm", self._llm, LLMState.IDLE)
        self._resolve_eye()

    # -- motion callbacks ----------------------------------------------------

    def on_motion_start(self, name: str = "") -> None:
        """A motion / animation is about to start."""
        self._transition("motion", self._motion, MotionState.EXECUTING)
        # Only override eye if currently idle/normal
        self._set_eye(EyeState.AWAKEN)

    def on_motion_end(self) -> None:
        """Motion execution finished."""
        self._transition("motion", self._motion, MotionState.IDLE)
        self._resolve_eye()

    # -- error callbacks -----------------------------------------------------

    def notify_error(self, source: str, message: str = "") -> None:
        """Report an error condition.

        Args:
            source: Which subsystem reported the error (e.g. "camera", "mic", "servo").
            message: Human-readable description.
        """
        self.logger.error("Error from %s: %s", source, message)
        self._transition("error", self._error, ErrorState.CRITICAL)
        self._set_eye(EyeState.ERROR)

    def clear_error(self, source: str = "") -> None:
        """Clear the current error condition."""
        self.logger.info("Error cleared by %s", source)
        self._transition("error", self._error, ErrorState.NONE)
        self._resolve_eye()

    # -- manual eye override -------------------------------------------------

    def set_eye(self, state: EyeState) -> bool:
        """Manually force an eye state ( bypasses automatic mapping )."""
        return self._set_eye(state)

    # -- internal helpers ----------------------------------------------------

    def _transition(self, subsystem: str, old: Any, new: Any) -> None:
        """Atomically update a subsystem state and fire the callback."""
        with self._lock:
            if subsystem == "voice":
                self._voice = new
            elif subsystem == "llm":
                self._llm = new
            elif subsystem == "motion":
                self._motion = new
            elif subsystem == "error":
                self._error = new

        if self._on_state_change is not None:
            try:
                self._on_state_change(StateEvent(subsystem, old, new))
            except Exception:
                self.logger.exception("State-change callback failed")

    def _set_eye(self, state: EyeState) -> bool:
        """Send a state to the eye controller if available."""
        if self._eye is None:
            return False
        try:
            return self._eye.set_state(state)
        except Exception:
            self.logger.exception("EyeController.set_state failed")
            return False

    def _resolve_eye(self) -> None:
        """Re-compute the correct eye state from all subsystem priorities.

        Priority (highest first):
            1. ERROR
            2. SPEAKING (voice) — formal RESPONSE
            3. GENERATING (LLM)
            4. EXECUTING (motion)
            5. LISTENING / RECOGNIZING (voice)
            6. IDLE → AWAIT
        """
        with self._lock:
            if self._error == ErrorState.CRITICAL:
                self._set_eye(EyeState.ERROR)
                return
            if self._voice == VoiceState.SPEAKING:
                self._set_eye(EyeState.RESPONSE)
                return
            if self._llm == LLMState.GENERATING:
                self._set_eye(EyeState.THINKING)
                return
            if self._motion == MotionState.EXECUTING:
                self._set_eye(EyeState.AWAKEN)
                return
            if self._voice in (VoiceState.LISTENING, VoiceState.RECOGNIZING):
                self._set_eye(EyeState.LISTENING)
                return
            # Default
            self._set_eye(EyeState.AWAIT)

    def _sync_loop(self) -> None:
        """Background thread: periodically re-evaluate eye state.

        This catches any edge cases where callbacks may have been missed.
        """
        while self._running:
            time.sleep(0.5)
            self._resolve_eye()
