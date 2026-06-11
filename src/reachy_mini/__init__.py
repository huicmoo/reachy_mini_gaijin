"""Reachy Mini SDK."""

from importlib.metadata import version

from reachy_mini.apps.app import ReachyMiniApp
from reachy_mini.io.eye_controller import EyeController, EyeState
from reachy_mini.reachy_mini import ReachyMini
from reachy_mini.utils.robot_state_machine import RobotStateMachine

__version__ = version("reachy_mini")

__all__ = [
    "ReachyMini",
    "ReachyMiniApp",
    "EyeController",
    "EyeState",
    "RobotStateMachine",
    "__version__",
]
