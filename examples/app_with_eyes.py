"""app_with_eyes.py — "墩墩" integration example.

Demonstrates the wake-word flow for robot "墩墩":
    1. Standby              → Eye_AWAIT
    2. "墩墩" detected      → Eye_AWAKEN + play "你好，我在" (NOT RESPONSE)
    3. Wake ack done        → Eye_LISTENING (after 2 s)
    4. User speaking        → Eye_LISTENING
    5. STT → LLM thinking   → Eye_THINKING
    6. TTS speaking         → Eye_RESPONSE (this IS the formal answer)
    7. Done                 → Eye_AWAIT

Run on Raspberry Pi 5 (ESP32 connected via USB):
    python examples/app_with_eyes.py

Or in simulation / without ESP32 (eyes are silently skipped):
    python examples/app_with_eyes.py --no-eyes
"""

import argparse
import logging
import time

from reachy_mini import EyeController, EyeState, ReachyMini, RobotStateMachine

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("app_with_eyes")


# ---------------------------------------------------------------------------
# Audio playback stub (replace with real TTS / media.play_sound in production)
# ---------------------------------------------------------------------------
def play_greeting(audio_path: str) -> None:
    """Stub for playing the wake-word greeting.

    In production, wire this to ``reachy_mini.media.play_sound()`` or your TTS engine.
    """
    logger.info("🔊 [AUDIO] Playing greeting: %s", audio_path)
    # Example production usage:
    # reachy_mini.media.play_sound(audio_path)


# ---------------------------------------------------------------------------
# Demo interaction loop
# ---------------------------------------------------------------------------
def run_demo(reachy: ReachyMini | None, sm: RobotStateMachine) -> None:
    """Simulate a full "墩墩" interaction cycle with eye state synchronisation."""

    logger.info("=== 墩墩 Demo start ===")
    logger.info("State: AWAIT (standby — waiting for '墩墩')")
    time.sleep(2)

    # ---- 1. Wake-word "墩墩" detected ------------------------------------
    logger.info("→ 🎙️ Wake-word detected: '墩墩'")
    logger.info("   Eye: AWAKEN  |  Audio: '你好，我在'  |  NOTE: This is NOT a response!")
    sm.on_wake_word_detected(greeting_audio="greetings/nihao_wozai.wav")
    # In production the greeting audio plays here; in demo we just wait
    time.sleep(2.5)  # Wait for greeting + eye transition

    # ---- 2. Now listening for the actual user question --------------------
    logger.info("→ 👂 Listening for user question…")
    sm.on_listening_start()
    time.sleep(3)  # Simulate user speaking

    # ---- 3. User finished speaking → STT / LLM processing ---------------
    logger.info("→ 🧠 Recognising speech & thinking…")
    sm.on_listening_end()               # → THINKING
    time.sleep(0.5)

    logger.info("→ 🤖 LLM generating response…")
    sm.on_llm_start()                   # → THINKING
    time.sleep(3)
    sm.on_llm_end()

    # ---- 4. TTS plays the FORMAL answer ---------------------------------
    logger.info("→ 🗣️ Playing TTS response (THIS is the formal answer)")
    sm.on_tts_start()                   # → RESPONSE (formal answer state)
    time.sleep(4)
    sm.on_tts_end()                     # → back to AWAIT

    # ---- 5. Positive emotion / happy action -----------------------------
    logger.info("→ 😊 Positive emotion detected — AWAKEN!")
    sm.set_eye(EyeState.AWAKEN)
    time.sleep(2)
    sm.set_eye(EyeState.AWAIT)

    # ---- 6. Simulate an error -------------------------------------------
    logger.info("→ ⚠️ Simulating camera error…")
    sm.notify_error("camera", "Camera device not found")    # → ERROR, 3 s lock
    time.sleep(4)                               # Wait for lock to expire
    sm.clear_error("system")                    # → AWAIT

    logger.info("=== 墩墩 Demo complete ===")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="墩墩 — Reachy Mini + ESP32 Eye integration demo")
    parser.add_argument(
        "--no-eyes",
        action="store_true",
        help="Disable eye controller (useful when ESP32 is not connected)",
    )
    parser.add_argument(
        "--eye-port",
        default="",
        help="Serial port for ESP32 (default: auto-detect). E.g. /dev/ttyACM0 or COM3",
    )
    parser.add_argument(
        "--sim",
        action="store_true",
        help="Spawn a simulated Reachy Mini instead of connecting to real hardware",
    )
    args = parser.parse_args()

    # ---- Eye controller -----------------------------------------------------
    eyes: EyeController | None = None

    if not args.no_eyes:
        eyes = EyeController(port=args.eye_port, auto_detect=(args.eye_port == ""))
        eyes.start()

        if eyes.get_current_state() is None:
            logger.warning(
                "No eye port found — continuing without eye display. "
                "Pass --no-eyes to suppress this warning."
            )
            eyes = None
    else:
        logger.info("Eye controller disabled (--no-eyes)")

    # ---- State machine -------------------------------------------------------
    sm = RobotStateMachine(
        eye_controller=eyes,
        on_state_change=lambda evt: logger.debug(
            "[STATE] %s: %s → %s", evt.subsystem, evt.old_state, evt.new_state
        ),
        on_play_audio=play_greeting,
    )
    sm.start()

    # ---- Robot ---------------------------------------------------------------
    try:
        with ReachyMini(spawn_daemon=args.sim, use_sim=args.sim) as reachy:
            reachy.enable_motors()
            reachy.wake_up()

            run_demo(reachy, sm)

            reachy.goto_sleep()
    except Exception as exc:
        logger.error("Robot connection failed: %s", exc)
        logger.info("Running demo in eye-only mode (no robot hardware)…")
        run_demo(None, sm)
    finally:
        sm.stop()
        if eyes is not None:
            eyes.stop()


if __name__ == "__main__":
    main()
