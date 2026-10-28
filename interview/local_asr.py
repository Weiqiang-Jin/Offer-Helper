"""Offline streaming Zipformer adapter; native sherpa resamples input continuously."""
from pathlib import Path
import numpy as np

MODEL_NAME = 'sherpa-onnx-streaming-zipformer-bilingual-zh-en-2023-02-20'
MODEL_DIR = Path(__file__).resolve().parents[1] / 'models' / MODEL_NAME
FILES = {'encoder': 'encoder-epoch-99-avg-1.int8.onnx',
         'decoder': 'decoder-epoch-99-avg-1.onnx',
         'joiner': 'joiner-epoch-99-avg-1.int8.onnx', 'tokens': 'tokens.txt'}

def model_files(config):
    root = Path(config.get('asr_model_dir') or MODEL_DIR)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[1] / root
    return {key: str(root / name) for key, name in FILES.items()}

def ready(config):
    return all(Path(path).is_file() for path in model_files(config).values())

class LocalRecognizer:
    def __init__(self, config, rate, emit, prefix):
        import sherpa_onnx
        self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
            **model_files(config), num_threads=2, sample_rate=16000, feature_dim=80,
            decoding_method='greedy_search', provider='cpu', enable_endpoint_detection=True,
            rule1_min_trailing_silence=2.4, rule2_min_trailing_silence=1.2,
            rule3_min_utterance_length=30)
        self.stream = self.recognizer.create_stream()
        self.rate, self.emit, self.prefix = rate, emit, prefix
        self.segment, self.last = 0, ''
    def start(self): pass
    def send_audio_frame(self, pcm):
        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768
        self.stream.accept_waveform(self.rate, samples)
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)
        text = self.recognizer.get_result(self.stream).strip()
        final = self.recognizer.is_endpoint(self.stream)
        if text and (text != self.last or final):
            self.emit({'type': 'transcript', 'id': self.prefix + str(self.segment),
                       'text': text, 'final': final})
        self.last = text
        if final:
            self.recognizer.reset(self.stream)
            self.segment += 1
            self.last = ''
    def stop(self):
        # Explicit stop discards unfinished speech, preventing an unwanted answer.
        self.stream = None
        self.recognizer = None
