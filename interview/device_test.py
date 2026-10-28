"""Bounded, local-only device tests. PCM stays in memory."""
import queue
import threading
import time
import uuid

import numpy as np
import pyaudiowpatch as pa

from .audio import mono_pcm


def output_for_loopback(p, index):
    loop = p.get_device_info_by_index(index)
    if not loop.get('isLoopbackDevice'):
        raise ValueError('请选择播放回环设备')
    name = loop['name'].removesuffix(' [Loopback]')
    matches = [d for d in p.get_device_info_generator()
               if d['hostApi'] == loop['hostApi'] and d['maxOutputChannels'] > 0
               and d['name'] == name and not d.get('isLoopbackDevice')]
    if len(matches) != 1:
        raise ValueError('无法唯一匹配播放设备，请刷新设备')
    return matches[0]


def playback_samples(clip, rate):
    if clip is None:
        t = np.arange(rate * 2) / rate
        # Gentle two-note tone, with short fades to avoid clicks.
        samples = .12 * np.sin(2 * np.pi * np.where(t < 1, 440, 660) * t)
        envelope = np.minimum(1, np.minimum(t, 2 - t) / .03)
        return (samples * envelope * 32767).astype(np.int16)
    pcm, source_rate = clip
    original = np.frombuffer(pcm, dtype=np.int16)
    if source_rate == rate:
        return original
    count = round(len(original) * rate / source_rate)
    return np.interp(np.arange(count) * source_rate / rate,
                     np.arange(len(original)), original).astype(np.int16)


class DeviceTest:
    def __init__(self, kind, index, emit, clip=None):
        self.id = uuid.uuid4().hex
        self.kind, self.index, self.emit, self.clip = kind, index, emit, clip
        self.halt = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.halt.set()
        self.thread.join(timeout=4)

    def send(self, **values):
        self.emit({'type': 'device_test', 'session': self.id, **values})

    def run(self):
        stream = None
        p = pa.PyAudio()
        result = {}
        try:
            if self.kind == 'record':
                d = p.get_device_info_by_index(self.index)
                if d.get('isLoopbackDevice') or d['maxInputChannels'] < 1:
                    raise ValueError('无效的麦克风')
                rate, channels = int(d['defaultSampleRate']), int(d['maxInputChannels'])
                frames = queue.Queue(maxsize=60)
                def callback(data, count, timing, status):
                    try:
                        frames.put_nowait((data, status))
                    except queue.Full:
                        self.halt.set()
                    return (None, pa.paComplete if self.halt.is_set() else pa.paContinue)
                stream = p.open(format=pa.paInt16, channels=channels, rate=rate,
                                input=True, input_device_index=self.index,
                                frames_per_buffer=rate // 10, stream_callback=callback)
                chunks, count = [], 0
                deadline = time.monotonic() + 7
                while count < rate * 5 and not self.halt.is_set():
                    if time.monotonic() > deadline:
                        raise RuntimeError('录音数据不足')
                    try:
                        data, status = frames.get(timeout=.2)
                    except queue.Empty:
                        continue
                    if status:
                        raise RuntimeError('录音数据中断')
                    pcm, level = mono_pcm(data, channels)
                    pcm = pcm[:(rate * 5 - count) * 2]
                    chunks.append(pcm)
                    count += len(pcm) // 2
                    self.send(phase='recording', remaining=max(0, 5 - count / rate), level=level,
                              message='请说话，正在录制 5 秒测试音频…')
                if not self.halt.is_set():
                    result = {'clip': (b''.join(chunks), rate), 'phase': 'ready',
                              'message': '录音完成。点击“播放录音”，通过所选播放设备试听。'}
            else:
                d = output_for_loopback(p, self.index)
                rate, channels = int(d['defaultSampleRate']), int(d['maxOutputChannels'])
                samples = playback_samples(self.clip, rate)
                stream = p.open(format=pa.paInt16, channels=channels, rate=rate,
                                output=True, output_device_index=int(d['index']))
                self.send(phase='playing', message='正在播放录音…' if self.clip else '正在播放两声提示音，请确认能否听到。')
                for offset in range(0, len(samples), rate // 20):
                    if self.halt.is_set():
                        break
                    chunk = samples[offset:offset + rate // 20]
                    stream.write(np.repeat(chunk[:, None], channels, axis=1).tobytes())
                result = {'phase': 'done', 'message': '播放完成。听到声音说明所选播放通路可用；听不到时请检查音量、静音及设备连接。'}
            if self.halt.is_set():
                result = {'phase': 'cancelled', 'message': '测试已停止，未完成的录音已丢弃。'}
        except Exception as exc:
            result = {'phase': 'error', 'message': f'设备测试失败（{type(exc).__name__}），请刷新设备并检查连接、权限和音量。'}
        finally:
            if stream:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            p.terminate()
            self.send(finished=True, **result)
