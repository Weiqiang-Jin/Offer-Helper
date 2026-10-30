import asyncio
import contextlib
import os
import secrets
import subprocess
import sys
import time
import uuid
from html import escape
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Depends, Header, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .audio import AudioSession, devices
from .device_test import DeviceTest
from .answers import stream_answer, punctuate_question, question_boundary, prepare_background
from .resume import extract_resume
from .settings import ModelSettings, load_settings, public_settings, update_settings
from .mobile import Companion
from .local_asr import ready as local_asr_ready
from .chatgpt import connection as chatgpt
from .paths import DATA_DIR, RESOURCE_DIR, overlay_command


def answer_config():
    return {**CONFIG, **({'chatgpt': chatgpt} if chatgpt.enabled else {})}

ROOT = RESOURCE_DIR
load_dotenv(DATA_DIR / ".env")
TOKEN = os.environ.get("INTERVIEW_TOKEN") or secrets.token_urlsafe(32)
CONFIG = {"asr_backend": os.getenv("ASR_BACKEND", "sherpa"),
          "asr_model_dir": os.getenv("SHERPA_MODEL_DIR", ""),"asr_key": os.getenv("DASHSCOPE_API_KEY", ""),
          "asr_url": os.getenv("ASR_WS_URL", "wss://dashscope.aliyuncs.com/api-ws/v1/inference"),
          "asr_model": os.getenv("ASR_MODEL", "paraformer-realtime-v2"),
          "llm_key": os.getenv("LLM_API_KEY", "") or os.getenv("DASHSCOPE_API_KEY", ""),
          "llm_url": os.getenv("LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
          "llm_model": os.getenv("LLM_MODEL", "qwen-plus")}
load_settings(CONFIG)


class State:
    def __init__(self):
        self.background = None
        self.preparing_background = False
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
                "configured": {"asr": local_asr_ready(CONFIG) if CONFIG["asr_backend"] == "sherpa" else bool(CONFIG["asr_key"]), "asr_backend": CONFIG["asr_backend"], "llm": bool(CONFIG["llm_key"]) or chatgpt.enabled}}

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
overlay_process = None


@asynccontextmanager
async def lifespan(app):
    yield
    if overlay_process and overlay_process.poll() is None:
        overlay_process.terminate()
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


@app.post('/api/overlay/start', dependencies=[Depends(auth)])
def overlay_start(request: Request):
    global overlay_process
    if sys.platform != 'win32':
        raise HTTPException(400, '本机悬浮窗仅支持 Windows。')
    if overlay_process is None or overlay_process.poll() is not None:
        overlay_process = subprocess.Popen(
            overlay_command(request.url.port),
            cwd=ROOT, creationflags=subprocess.CREATE_NO_WINDOW,
            env={**os.environ, 'INTERVIEW_OVERLAY_TOKEN': TOKEN})
    return {'running': True}


@app.post('/api/overlay/stop', dependencies=[Depends(auth)])
def overlay_stop():
    global overlay_process
    if overlay_process and overlay_process.poll() is None:
        overlay_process.terminate()
    overlay_process = None
    return {'running': False}


@app.get('/api/overlay/status', dependencies=[Depends(auth)])
def overlay_status():
    return {'running': bool(overlay_process and overlay_process.poll() is None)}


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


@app.get('/api/chatgpt', dependencies=[Depends(auth)])
async def chatgpt_status():
    return chatgpt.status()


@app.post('/api/chatgpt/login', dependencies=[Depends(auth)])
async def chatgpt_login(request: Request):
    if state.answer_task and not state.answer_task.done():
        raise HTTPException(409, '请先等待回答完成或取消生成。')
    return {'url': chatgpt.begin(request.url.port or 80)}


@app.get('/auth/callback')
async def chatgpt_callback(request: Request):
    try:
        await chatgpt.finish(dict(request.query_params))
        message = '登录完成。请返回 offer helper，刷新账号模型列表并选择是否启用。'
    except Exception:
        message = chatgpt.login_error or '登录未完成，请返回 offer helper 重新登录。'
    return HTMLResponse('<meta charset="utf-8"><title>offer helper</title><p>' + escape(message) + '</p><p>请返回软件查看状态；重新登录请从软件按钮发起，不要刷新本页。</p>',
        headers={'Cache-Control': 'no-store', 'Referrer-Policy': 'no-referrer'})


@app.post('/api/chatgpt/models', dependencies=[Depends(auth)])
async def chatgpt_models():
    try:
        await chatgpt.list_models()
    except Exception:
        raise HTTPException(400, '无法读取 ChatGPT 模型，请检查网络及计划授权，或重新登录。')
    return chatgpt.status()


class ChatGPTSelection(BaseModel):
    enabled: bool
    model: str = ''


@app.post('/api/chatgpt/select', dependencies=[Depends(auth)])
async def chatgpt_select(body: ChatGPTSelection):
    if state.answer_task and not state.answer_task.done():
        raise HTTPException(409, '请先等待回答完成或取消生成。')
    if body.enabled and (not chatgpt.status()['sharing'] or body.model not in [m['slug'] for m in chatgpt.models]):
        raise HTTPException(400, '请先登录、授权计划使用并选择账号可用模型。')
    chatgpt.enabled, chatgpt.model = body.enabled, body.model
    return chatgpt.status()


@app.post('/api/chatgpt/logout', dependencies=[Depends(auth)])
async def chatgpt_logout():
    if state.answer_task and not state.answer_task.done():
        raise HTTPException(409, '请先取消生成。')
    chatgpt.logout()
    return chatgpt.status()


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
        if body.transcribe and CONFIG['asr_backend'] == 'sherpa' and not local_asr_ready(CONFIG):
            raise HTTPException(400, '本地语音模型未安装，请运行 download-asr.bat 下载模型后重试。')
        if body.transcribe and CONFIG['asr_backend'] not in ('sherpa', 'dashscope'):
            raise HTTPException(400, 'ASR_BACKEND 仅支持 sherpa 或 dashscope。')
        if body.transcribe and CONFIG['asr_backend'] == 'dashscope' and not CONFIG["asr_key"]:
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


class QuestionBoundary(BaseModel):
    previous: str = Field(min_length=1, max_length=8000)
    incoming: str = Field(min_length=1, max_length=8000)
    gap_seconds: float = Field(default=0, ge=0, le=86400)


@app.post('/api/question/boundary', dependencies=[Depends(auth)])
async def classify_question(body: QuestionBoundary):
    if not CONFIG['llm_key'] and not chatgpt.enabled:
        return {'decision': 'uncertain'}
    return {'decision': await question_boundary(answer_config(), body.previous, body.incoming, body.gap_seconds)}


class BackgroundInput(BaseModel):
    filename: str = Field(default='', max_length=255)
    file_data: str = Field(default='', max_length=7000000)
    profile: str = Field(default='', max_length=24000)


@app.get('/api/background', dependencies=[Depends(auth)])
async def get_background():
    return {'background': state.background}


@app.post('/api/background', dependencies=[Depends(auth)])
async def build_background(body: BackgroundInput):
    if state.preparing_background or state.audio or (state.answer_task and not state.answer_task.done()):
        raise HTTPException(409, '请在开始面试前建立上下文，先停止采集或等待当前任务完成。')
    if not CONFIG['llm_key'] and not chatgpt.enabled:
        raise HTTPException(400, '请先配置或启用回答模型。')
    state.preparing_background = True
    try:
        resume = await asyncio.to_thread(extract_resume, body.filename, body.file_data) if body.file_data else ''
        if not resume and not body.profile.strip():
            raise ValueError('请先选择简历附件或填写个人材料与岗位。')
        summary = await prepare_background(answer_config(), resume, body.profile)
        state.background = {'id': uuid.uuid4().hex, 'summary': summary, 'filename': body.filename}
        return {'background': state.background}
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    except Exception:
        raise HTTPException(502, '预读材料失败，请检查当前模型连接或额度后重试。')
    finally:
        state.preparing_background = False


@app.post('/api/background/clear', dependencies=[Depends(auth)])
async def clear_background():
    if state.preparing_background:
        raise HTTPException(409, '请等待材料预读完成。')
    state.background = None
    return {'background': None}


class Answer(BaseModel):
    question: str = Field(min_length=1, max_length=8000)
    profile: str = Field(default="", max_length=24000)
    transcribed: bool = False
    context_id: str = ''


async def generate(body):
    started = time.monotonic()
    state.answer_status = "generating"
    state.answer, state.question = "", body.question
    state.current_question = body.question
    state.publish({"type": "answer_start", "question": body.question})
    try:
        config = answer_config()
        question = await punctuate_question(config, body.question) if body.transcribed else body.question
        state.question = state.current_question = question
        if question != body.question:
            state.publish({"type": "question_normalized", "original": body.question, "question": question})
        async for chunk in stream_answer(config, question, body.profile, list(state.history)):
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
    if state.preparing_background:
        raise HTTPException(409, '正在预读面试材料，请稍候。')
    if body.context_id:
        if not state.background or body.context_id != state.background['id']:
            raise HTTPException(409, '面试上下文已失效，请重新建立或刷新页面。')
        body.profile = state.background['summary']
    if not body.question.strip():
        raise HTTPException(400, "请先输入或选取一个问题。")
    if not CONFIG["llm_key"] and not chatgpt.enabled:
        raise HTTPException(400, "尚未配置模型密钥，请在回答模型设置中配置，或配置 .env。")
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

