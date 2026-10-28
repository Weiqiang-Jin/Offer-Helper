"""Windows endpoint loopback + microphone. No audio files are written."""
import queue
import threading
import time
import uuid

import numpy as np
import pyaudiowpatch as pa


def devices():
    with pa.PyAudio() as p:
        api = p.get_host_api_info_by_type(pa.paWASAPI)
        result = {"remote": [], "self": []}
        for d in p.get_device_info_generator():
            if d["hostApi"] != api["index"] or d["maxInputChannels"] < 1:
                continue
            role = "remote" if d.get("isLoopbackDevice") else "self"
            result[role].append({"id": d["index"], "name": d["name"],
                                 "rate": int(d["defaultSampleRate"])})
        return result


def mono_pcm(data, channels):
    samples = np.frombuffer(data, dtype=np.int16).reshape(-1, channels)
    mono = np.rint(samples.astype(np.float32).mean(axis=1)).astype(np.int16)
    level = float(np.sqrt(np.mean((mono.astype(np.float32) / 32768) ** 2)))
    return mono.tobytes(), level


class AudioSession:
    def __init__(self, selected, transcribe, config, emit):
        self.selected, self.transcribe, self.config, self.emit = selected, transcribe, config, emit
        self.halt = threading.Event()
        self.threads = []
        self.id = uuid.uuid4().hex

    def start(self):
        for role, index in self.selected.items():
            t = threading.Thread(target=self._run, args=(role, index), daemon=True)
            self.threads.append(t)
            t.start()

    def stop(self):
        self.halt.set()
        for t in self.threads:
            t.join(timeout=4)

    def _run(self, role, index):
        frames = queue.Queue(maxsize=30)
        recognizer = None
        stream = None
        p = pa.PyAudio()
        closing = False
        prefix = f"{self.id}:{role}:"
        def send(event):
            self.emit({**event, "role": role, "session": self.id})
        try:
            d = p.get_device_info_by_index(index)
            is_loop = bool(d.get("isLoopbackDevice"))
            if is_loop != (role == "remote") or d["maxInputChannels"] < 1:
                raise ValueError("所选设备类型不正确，请刷新设备列表。")
            channels, rate = int(d["maxInputChannels"]), int(d["defaultSampleRate"])
            if self.transcribe and self.config.get('asr_backend', 'sherpa') == 'sherpa':
                from .local_asr import LocalRecognizer
                recognizer = LocalRecognizer(self.config, rate, send, prefix)
            elif self.transcribe:
                import dashscope
                from dashscope.audio.asr import Recognition, RecognitionCallback, RecognitionResult
                dashscope.base_websocket_api_url = self.config["asr_url"]
                owner = self
                class Callback(RecognitionCallback):
                    def on_event(self, result):
                        sentence = result.get_sentence()
                        if sentence and sentence.get("text"):
                            send({"type": "transcript", "id": prefix + str(sentence.get("begin_time", 0)),
                                  "text": sentence["text"], "final": RecognitionResult.is_sentence_end(sentence)})
                    def on_error(self, result):
                        send({"type": "error", "message": "语音服务失败，请检查密钥、地域、额度和网络。"})
                        owner.halt.set()
                    def on_close(self):
                        if not closing and not owner.halt.is_set():
                            send({"type": "error", "message": "语音服务连接已断开，请停止后重新开始。"})
                            owner.halt.set()
                recognizer = Recognition(model=self.config["asr_model"], format="pcm", sample_rate=rate,
                                         api_key=self.config["asr_key"], callback=Callback(), heartbeat=True,
                                         language_hints=["zh", "en"])
                recognizer.start()
            def callback(data, count, timing, status):
                if self.halt.is_set():
                    return (None, pa.paComplete)
                try:
                    frames.put_nowait(data)
                except queue.Full:
                    send({"type": "error", "message": "音频处理积压，已停止采集，请检查电脑负载或网络后重试。"})
                    self.halt.set()
                if status:
                    send({"type": "warning", "message": "音频设备报告数据中断，请检查字幕是否缺失。"})
                return (None, pa.paContinue)
            stream = p.open(format=pa.paInt16, channels=channels, rate=rate, input=True,
                            input_device_index=index, frames_per_buffer=int(rate / 10), stream_callback=callback)
            send({"type": "audio_status", "status": "running"})
            last_meter = 0
            while not self.halt.is_set():
                try:
                    data = frames.get(timeout=0.2)
                except queue.Empty:
                    if not stream.is_active():
                        raise RuntimeError("音频设备已断开，请重新选择设备。")
                    if recognizer:
                        # Loopback may deliver no buffers while the endpoint is silent.
                        recognizer.send_audio_frame(bytes(int(rate * 0.2) * 2))
                    send({"type": "level", "value": 0})
                    continue
                pcm, level = mono_pcm(data, channels)
                if time.monotonic() - last_meter > 0.15:
                    send({"type": "level", "value": level})
                    last_meter = time.monotonic()
                if recognizer:
                    recognizer.send_audio_frame(pcm)
        except Exception as exc:
            # Avoid sending SDK errors that could include request headers / credentials.
            send({"type": "error", "message": f"音频/识别启动失败（{type(exc).__name__}），请检查设备、服务配置和网络。"})
            self.halt.set()
        finally:
            closing = True
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            p.terminate()
            if recognizer:
                try:
                    recognizer.stop()
                except Exception:
                    pass
            send({"type": "audio_status", "status": "stopped"})

