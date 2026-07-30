from __future__ import annotations

import pytest

from app.recording.state import InvalidStateTransitionError, RecordingState, validate_transition


def test_idle_to_starting_allowed() -> None:
    validate_transition(RecordingState.IDLE, RecordingState.STARTING)


def test_idle_to_recording_rejected() -> None:
    with pytest.raises(InvalidStateTransitionError):
        validate_transition(RecordingState.IDLE, RecordingState.RECORDING)


def test_recording_to_stopping_allowed() -> None:
    validate_transition(RecordingState.RECORDING, RecordingState.STOPPING)


def test_recording_cannot_restart() -> None:
    with pytest.raises(InvalidStateTransitionError):
        validate_transition(RecordingState.RECORDING, RecordingState.STARTING)


def test_finalizing_to_idle_allowed() -> None:
    validate_transition(RecordingState.FINALIZING, RecordingState.IDLE)


def test_error_only_recovers_to_idle() -> None:
    validate_transition(RecordingState.ERROR, RecordingState.IDLE)
    with pytest.raises(InvalidStateTransitionError):
        validate_transition(RecordingState.ERROR, RecordingState.RECORDING)


def test_starting_can_abort_back_to_idle() -> None:
    # Kamera algılama/pipeline başlatma başarısız olursa manager bu geçişi kullanır.
    validate_transition(RecordingState.STARTING, RecordingState.IDLE)
