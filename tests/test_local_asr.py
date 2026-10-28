import numpy as np
import sherpa_onnx
from interview.local_asr import LocalRecognizer, ready


def test_streaming_partial_final_reset_and_pcm(monkeypatch):
    outputs, samples, options = [], [], {}
    class Stream:
        def accept_waveform(self, rate, data): samples.append((rate, data))
    class Recognizer:
        count = 0
        resets = 0
        def create_stream(self): return Stream()
        def is_ready(self, stream): return False
        def get_result(self, stream):
            self.count += 1
            return ['介绍', '介绍项目', '介绍项目', '为什么'][self.count - 1]
        def is_endpoint(self, stream): return self.count == 3
        def reset(self, stream): self.resets += 1
    recognizer = Recognizer()
    def factory(**kwargs):
        options.update(kwargs)
        return recognizer
    monkeypatch.setattr(sherpa_onnx.OnlineRecognizer, 'from_transducer', factory)
    local = LocalRecognizer({}, 48000, outputs.append, 'session:remote:')
    for _ in range(4): local.send_audio_frame(np.array([0, 32767, -32768], dtype=np.int16).tobytes())
    assert [x['final'] for x in outputs] == [False, False, True, False]
    assert outputs[0]['id'] == outputs[2]['id']
    assert outputs[3]['id'] != outputs[2]['id']
    assert samples[0][0] == 48000
    assert samples[0][1].min() == -1
    assert options['provider'] == 'cpu'
    assert recognizer.resets == 1
    local.stop()
    assert len(outputs) == 4


def test_missing_model(tmp_path):
    assert not ready({'asr_model_dir': str(tmp_path)})
