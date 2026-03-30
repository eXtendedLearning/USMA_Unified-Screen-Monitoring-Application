"""Audio feedback module for USMA."""

import logging
import threading

import numpy as np

try:
    import sounddevice as sd
    SOUND_DEVICE_AVAILABLE = True
except (ImportError, OSError) as e:
    SOUND_DEVICE_AVAILABLE = False

logger = logging.getLogger(__name__)


class AudioFeedback:
    """Manages continuous audio tone feedback for hit detection."""

    def __init__(self):
        self.audio_feedback_enabled = False
        self.audio_stream = None
        self.audio_phase = 0
        self.audio_frequency = 400
        self.audio_lock = threading.Lock()
        self.sample_rate = 44100

    def set_enabled(self, enabled: bool):
        self.audio_feedback_enabled = enabled
        if not enabled:
            self.stop()

    def _audio_callback(self, outdata, frames, time_info, status):
        try:
            if status:
                logger.warning(f"Audio stream status: {status}")
            t = (self.audio_phase + np.arange(frames)) / self.sample_rate
            t_reshaped = np.reshape(t, (-1, 1))
            amplitude = np.iinfo(np.int16).max * 0.3
            outdata[:] = amplitude * np.sin(2 * np.pi * self.audio_frequency * t_reshaped)
            self.audio_phase += frames
        except Exception as e:
            logger.error(f"Audio callback error: {e}")
            outdata.fill(0)

    def start(self):
        if not SOUND_DEVICE_AVAILABLE or self.audio_stream is not None:
            return
        try:
            self.audio_phase = 0
            stream = sd.OutputStream(
                samplerate=self.sample_rate, channels=1,
                callback=self._audio_callback, dtype='int16'
            )
            self.audio_stream = stream
            stream.start()
            logger.info("Continuous audio feedback started.")
        except Exception as e:
            logger.error(f"Failed to start audio stream: {e}")
            self.audio_stream = None

    def stop(self):
        stream = self.audio_stream
        if stream is not None:
            try:
                stream.stop()
                stream.close()
                logger.info("Continuous audio feedback stopped.")
            except Exception as e:
                logger.error(f"Error stopping audio stream: {e}")
            finally:
                self.audio_stream = None
