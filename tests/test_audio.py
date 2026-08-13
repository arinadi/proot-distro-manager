"""installer/audio.py: test-tone generation.

    python tests/test_audio.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from support import check, run

from installer import audio


def test_audio_test_tone_is_valid() -> None:
    """The tone is generated rather than shipped, so it must be a real WAV."""
    import wave

    path = os.path.join(tempfile.gettempdir(), "pdm-tone-probe.wav")
    check(audio.write_test_tone(path, seconds=0.2), "the tone was not written")
    with wave.open(path) as handle:
        check(handle.getnchannels() == 1, "expected mono")
        check(handle.getsampwidth() == 2, "expected 16-bit samples")
        check(handle.getnframes() > 0, "the tone has no frames")
    os.remove(path)


TESTS = [
    test_audio_test_tone_is_valid,
]

if __name__ == "__main__":
    sys.exit(run(TESTS, label=os.path.basename(__file__)))
