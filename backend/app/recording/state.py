"""Kayıt durum makinesi.

Şartname böl. 13'teki geçiş diyagramını birebir uygular:
idle -> starting -> recording -> stopping -> finalizing -> idle
Herhangi bir aşamada error'a düşülebilir; error'dan yalnızca idle'a
dönülebilir (servis/operatör müdahalesiyle, örn. kamera tekrar erişilebilir
hale geldiğinde).
"""

from __future__ import annotations

from enum import Enum

from app.core.errors import AppError, ErrorCode


class RecordingState(str, Enum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"
    FINALIZING = "finalizing"
    ERROR = "error"


_VALID_TRANSITIONS: dict[RecordingState, frozenset[RecordingState]] = {
    RecordingState.IDLE: frozenset({RecordingState.STARTING}),
    RecordingState.STARTING: frozenset({RecordingState.RECORDING, RecordingState.ERROR, RecordingState.IDLE}),
    RecordingState.RECORDING: frozenset({RecordingState.STOPPING, RecordingState.ERROR}),
    RecordingState.STOPPING: frozenset({RecordingState.FINALIZING, RecordingState.ERROR}),
    RecordingState.FINALIZING: frozenset({RecordingState.IDLE, RecordingState.ERROR}),
    RecordingState.ERROR: frozenset({RecordingState.IDLE}),
}


class InvalidStateTransitionError(AppError):
    def __init__(self, current: RecordingState, target: RecordingState) -> None:
        super().__init__(
            ErrorCode.INVALID_STATE_TRANSITION,
            f"Geçersiz durum geçişi: {current.value} -> {target.value}",
            status_code=409,
            details={"current": current.value, "target": target.value},
        )


def validate_transition(current: RecordingState, target: RecordingState) -> None:
    allowed = _VALID_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise InvalidStateTransitionError(current, target)
