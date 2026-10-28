import asyncio
import contextlib
import os
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .audio import AudioSession, devices
from .device_test import DeviceTest
from .answers import stream_answer
from .settings import ModelSettings, load_settings, public_settings, update_settings
from .mobile import Companion

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
TOKEN = os.environ.get("INTERVIEW_TOKEN") or secrets.token_urlsafe(32)
CONFIG = {"asr_key": os.getenv("DASHSCOPE_API_KEY", ""),
          "asr_url": os.getenv("ASR_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/inference"),
          "asr_model": os.getenv("ASR_MODEL", "paraformer-realtime-v2"),
          "llm_key": os.getenv("LLM_API_KEY", "") or os.getenv("DASHSCOPE_API_KEY", ""),
          "llm_url": os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
          "llm_model": os.getenv("LLM_MODEL", "qwen-plus")}
load_settings(CONFIG)


class State:
    def __init__(self):
        self.history = []
        self.answer = ""
        self.question = ""
        self.current_question = ""
        self.answer_status = "idle"
        self.audio = None
        self.answer_task = None
        self.clients = set()
        self.lock = asyncio.Lock()
        self.levels = {"remote": 0}
        self.status = {"remote": "stopped"}
        self.mode = "idle"
        self.test_clip = None
        self.test_status = {'phase': 'idle', 'message': '可播放提示音，或录制 5 秒麦克风测试音频。'}

    def snapshot(self):
        return {"type": "snapshot", "history": self.history, "answer": self.answer,
                "question": self.question, "answer_status": self.answer_status,
                "audio_status": self.status, "mode": self.mode,
                "device_test": {**self.test_status, 'has_clip': self.test_clip is not None},
                "configured": {"asr": bool(CONFIG["asr_key"]), "llm": bool(CONFIG["llm_key"])}}

    def publish(self, event):
        for q in list(self.clients):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Replace stale events with a consistent snapshot for slow clients.
                while not q.empty():
                    q.get_nowait()
                q.put_nowait(self.snapshot())

    def event(self, event):
        if event.get("session") and (not self.audio or event["session"] != self.audio.id):
            return
        if event['type'] == 'device_test':
            event = dict(event)
            clip = event.pop('clip', None)
            if clip is not None:
                self.test_clip = clip
            self.test_status = {k: v for k, v in event.items() if k not in ('session', 'type')}
            event['has_clip'] = self.test_clip is not None
            if event.get('finished'):
                self.audio = None
                self.mode = 'idle'
                self.status = {'remote': 'stopped', 'self': 'stopped'}
            self.publish(event)
            return
        if event["type"] == "transcript":
            previous = next((x for x in self.history if x["id"] == event["id"]), None)
            if previous:
                if previous.get("final"):
                    return
                previous.update(event)
            else:
                self.history.append(event)
                self.history = self.history[-200:]
            self.current_question = event['text']
        elif event["type"] == "audio_status":
            self.status[event["role"]] = event["status"]
        self.publish(event)


state = State()
companion = Companion(state)


@asynccontextmanager
async def lifespan(app):
    yield
    await companion.disable()
    if state.answer_task:
        state.answer_task.cancel()
    if state.audio:
        await asyncio.to_thread(state.audio.stop)


app = FastAPI(title="offer helper", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")


@app.middleware('http')
async def no_cached_ui(request, call_next):
    response = await call_next(request)
    if request.url.path == '/' or request.url.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-store'
    return response


def auth(authorization: str = Header(default="")):
    if not secrets.compare_digest(authorization, "Bearer " + TOKEN):
        raise HTTPException(401, "请使用启动脚本打开的链接连接。")


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


@app.get("/api/state", dependencies=[Depends(auth)])
def get_state():
    return state.snapshot()


@app.get('/api/mobile', dependencies=[Depends(auth)])
async def mobile_status():
    return companion.status()


@app.post('/api/mobile/enable', dependencies=[Depends(auth)])
async def mobile_enable():
    import io
    import qrcode
    import qrcode.image.svg
    result = await companion.enable()
    result['qrs'] = []
    for link in result['links']:
        output = io.BytesIO()
        qrcode.make(link, image_factory=qrcode.image.svg.SvgPathImage).save(output)
        result['qrs'].append(output.getvalue().decode())
    return result


@app.post('/api/mobile/disable', dependencies=[Depends(auth)])
async def mobile_disable():
    return await companion.disable()


@app.get('/api/model', dependencies=[Depends(auth)])
def get_model():
    return public_settings(CONFIG)


@app.post('/api/model', dependencies=[Depends(auth)])
async def set_model(body: ModelSettings):
    if state.answer_task and not state.answer_task.done():
        raise HTTPException(409, '请先等待回答完成或取消生成，再切换模型。')
    try:
        result = update_settings(CONFIG, body)
    except OSError:
        raise HTTPException(500, '无法保存本机配置，请检查目录写入权限。')
    state.publish({'type': 'model_settings', **result})
    return result


@app.get("/api/devices", dependencies=[Depends(auth)])
def get_devices():
    try:
        return devices()
    except Exception:
        raise HTTPException(503, "无法读取音频设备，请检查 Windows 音频服务。")


class Start(BaseModel):
    remote: int | None = Field(default=None, ge=0)
    microphone: int | None = Field(default=None, ge=0)
    transcribe: bool = False


class TestDevice(BaseModel):
    kind: Literal['tone', 'record', 'replay']
    device: int = Field(ge=0)


@app.post('/api/audio/test', dependencies=[Depends(auth)])
async def test_device(body: TestDevice):
    async with state.lock:
        if state.audio:
            raise HTTPException(409, '请先停止当前采集或设备测试。')
        available = await asyncio.to_thread(devices)
        role = 'self' if body.kind == 'record' else 'remote'
        if body.device not in [d['id'] for d in available[role]]:
            raise HTTPException(400, '设备已变化，请连接设备后刷新。')
        if body.kind == 'replay' and state.test_clip is None:
            raise HTTPException(400, '请先完成 5 秒麦克风录音。')
        if body.kind == 'record':
            state.test_clip = None
        loop = asyncio.get_running_loop()
        state.audio = DeviceTest(body.kind, body.device,
            lambda event: loop.call_soon_threadsafe(state.event, event),
            state.test_clip if body.kind == 'replay' else None)
        state.mode = 'device_test'
        state.status = {role: 'starting'}
        state.test_status = {'phase': 'starting', 'message': '正在打开设备…'}
        state.audio.start()
        state.publish(state.snapshot())
        return {'ok': True}


@app.post("/api/audio/start", dependencies=[Depends(auth)])
async def start_audio(body: Start):
    async with state.lock:
        if state.audio:
            raise HTTPException(409, "请先停止当前采集。")
        if body.transcribe and not CONFIG["asr_key"]:
            raise HTTPException(400, "尚未配置 DASHSCOPE_API_KEY，可先运行音量测试。")
        available = await asyncio.to_thread(devices)
        selected = {}
        if body.remote is not None:
            selected['remote'] = body.remote
        if body.microphone is not None and not body.transcribe:
            selected['self'] = body.microphone
        if not selected or (body.transcribe and body.remote is None):
            raise HTTPException(400, '请选择需要采集的设备。实时转写需要会议播放设备。')
        if any(index not in [d['id'] for d in available[role]] for role, index in selected.items()):
            raise HTTPException(400, "设备已变化，请刷新后重新选择。")
        loop = asyncio.get_running_loop()
        state.audio = AudioSession(selected, body.transcribe, CONFIG,
                                   lambda event: loop.call_soon_threadsafe(state.event, event))
        state.status = {'remote': 'stopped', 'self': 'stopped', **{role: 'starting' for role in selected}}
        state.mode = "transcribe" if body.transcribe else "meter"
        state.audio.start()
        state.publish(state.snapshot())
        return {"ok": True}


@app.post("/api/audio/stop", dependencies=[Depends(auth)])
async def stop_audio():
    async with state.lock:
        if state.audio:
            await asyncio.to_thread(state.audio.stop)
            state.audio = None
        state.status = {"remote": "stopped"}
        state.mode = "idle"
        state.test_status = {'phase': 'idle', 'message': '设备测试 / 采集已停止。'}
        state.publish(state.snapshot())
    return {"ok": True}


class Transcript(BaseModel):
    role: Literal["remote"] = "remote"
    text: str = Field(min_length=1, max_length=8000)


@app.post("/api/transcript", dependencies=[Depends(auth)])
async def add_transcript(body: Transcript):
    if not body.text.strip():
        raise HTTPException(400, "请输入内容。")
    state.event({"type": "transcript", "id": uuid.uuid4().hex, "role": body.role,
                 "text": body.text.strip(), "final": True, "source": "manual"})
    return {"ok": True}


class Answer(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    profile: str = Field(default="", max_length=24000)
    demo: bool = False


async def generate(body):
    started = time.monotonic()
    state.answer_status = "generating"
    state.answer, state.question = "", body.question
    state.current_question = body.question
    state.publish({"type": "answer_start", "question": body.question, "demo": body.demo})
    try:
        if body.demo:
            text = ("【界面演示 · 固定模板，并非模型回答】\n\n当前问题：" + body.question +
                    "\n\n回答组织方式\n1. 先用一句话直接回应问题。\n2. 解释关键原理或方案取舍。\n3. 补充你真实参与的项目案例。\n\n"
                    "口述练习：我的理解是……我主要考虑了……在实际项目中，我负责的部分是……\n\n配置模型服务后，这里会流式显示针对问题和个人材料生成的答案。")
            for chunk in [text[i:i+12] for i in range(0, len(text), 12)]:
                await asyncio.sleep(0.025)
                state.answer += chunk
                state.publish({"type": "answer_delta", "text": chunk})
        else:
            async for chunk in stream_answer(dict(CONFIG), body.question, body.profile, list(state.history)):
                state.answer += chunk
                state.publish({"type": "answer_delta", "text": chunk})
        if not state.answer.strip():
            raise RuntimeError("Empty response")
        state.answer_status = "done"
    except asyncio.CancelledError:
        state.answer_status = "cancelled"
    except Exception as exc:
        state.answer_status = "error"
        state.publish({"type": "error", "message": f"回答生成失败（{type(exc).__name__}），请检查模型名称、密钥、额度和网络。"})
    finally:
        state.publish({"type": "answer_end", "status": state.answer_status,
                       "elapsed": round(time.monotonic() - started, 1)})


@app.post("/api/answer", dependencies=[Depends(auth)])
async def answer(body: Answer):
    if not body.question.strip():
        raise HTTPException(400, "请先输入或选取一个问题。")
    if not body.demo and not CONFIG["llm_key"]:
        raise HTTPException(400, "尚未配置模型密钥，请配置 .env，或勾选固定模板演示。")
    if state.answer_task and not state.answer_task.done():
        raise HTTPException(409, "已有回答正在生成，请先取消。")
    state.answer_task = asyncio.create_task(generate(body))
    return {"ok": True}


@app.post("/api/answer/cancel", dependencies=[Depends(auth)])
async def cancel():
    if state.answer_task and not state.answer_task.done():
        state.answer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await state.answer_task
    return {"ok": True}


@app.post("/api/reset", dependencies=[Depends(auth)])
async def reset():
    if state.audio or (state.answer_task and not state.answer_task.done()):
        raise HTTPException(409, "请先停止采集和回答，再清空会话。")
    state.history.clear()
    state.test_clip = None
    state.answer = state.question = ""
    state.current_question = ""
    state.answer_status = "idle"
    state.publish(state.snapshot())
    return {"ok": True}


@app.websocket("/ws")
async def websocket(ws: WebSocket):
    await ws.accept()
    try:
        initial = await asyncio.wait_for(ws.receive_json(), timeout=5)
        if not secrets.compare_digest(str(initial.get("token", "")), TOKEN):
            await ws.close(code=1008)
            return
    except (asyncio.TimeoutError, WebSocketDisconnect, ValueError):
        await ws.close(code=1008)
        return
    q = asyncio.Queue(maxsize=256)
    state.clients.add(q)
    async def sender():
        await ws.send_json(state.snapshot())
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15)
            except asyncio.TimeoutError:
                event = {"type": "ping"}
            await ws.send_json(event)
    task = asyncio.create_task(sender())
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        state.clients.discard(q)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, RuntimeError):
            await task

