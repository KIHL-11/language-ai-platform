r"""
英语听说训练器 · 后端
第一步：提供自然 TTS（Edge TTS 神经网络嗓音），并返回每个词的时间点用于高亮。
跑法：  .venv\Scripts\python -m uvicorn app:app --port 8000
然后浏览器打开 http://localhost:8000
"""
import base64
import math
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Optional

import edge_tts
from fastapi import FastAPI, File, Form, UploadFile, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from services.media.extractor import extract_media
from services.schemas.lesson import SourceLanguage

BASE = Path(__file__).parent
MEDIA = BASE / "media"
MEDIA.mkdir(exist_ok=True)
app = FastAPI(title="英语听说训练器")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)
from services.api.lesson_api import router as lesson_router
from services.api.chunk_api import router as chunk_router
from services.api.realtime_api import router as realtime_router
from services.api.media_discovery_api import router as media_discovery_router

app.include_router(
    lesson_router,
    prefix="/api"
)
app.include_router(
    chunk_router,
    prefix="/api"
)
app.include_router(
    realtime_router,
    prefix="/api"
)
app.include_router(
    media_discovery_router,
    prefix="/api"
)
# 口音 + 性别 → 默认嗓音（用对话级新嗓音，比老的 Aria/Guy 自然很多）
VOICES = {
    ("en-US", "female"): "en-US-AvaNeural",
    ("en-US", "male"):   "en-US-AndrewNeural",
    ("en-GB", "female"): "en-GB-SoniaNeural",
    ("en-GB", "male"):   "en-GB-RyanNeural",
}

# 给前端选的精选自然嗓音
VOICE_LIST = {
    "en-US": [
        {"id": "en-US-AvaNeural",     "label": "Ava（女·自然）"},
        {"id": "en-US-EmmaNeural",    "label": "Emma（女·亲切）"},
        {"id": "en-US-AndrewNeural",  "label": "Andrew（男·自然）"},
        {"id": "en-US-BrianNeural",   "label": "Brian（男·随和）"},
    ],
    "en-GB": [
        {"id": "en-GB-SoniaNeural",   "label": "Sonia（女·自然）"},
        {"id": "en-GB-LibbyNeural",   "label": "Libby（女·年轻）"},
        {"id": "en-GB-RyanNeural",    "label": "Ryan（男·自然）"},
        {"id": "en-GB-ThomasNeural",  "label": "Thomas（男·沉稳）"},
    ],
}

class TTSReq(BaseModel):
    text: str
    accent: str = "en-US"        # en-US / en-GB
    gender: str = "female"        # female / male（无指定 voice 时的兜底）
    voice: Optional[str] = None   # 指定具体嗓音，优先于 accent/gender
    rate: float = 1.0             # 0.5 ~ 2.0

def rate_to_str(rate: float) -> str:
    # edge-tts 的语速是百分比字符串： 1.0->+0%  1.2->+20%  0.5->-50%
    pct = int(round((rate - 1.0) * 100))
    return f"{pct:+d}%"

async def synth(text: str, voice: str, rate: float):
    """合成音频，同时收集每个词的时间点（WordBoundary）。"""
    communicate = edge_tts.Communicate(text, voice, rate=rate_to_str(rate), boundary="WordBoundary")
    audio = bytearray()
    marks = []
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio.extend(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            # offset / duration 单位是 100 纳秒（tick），换算成秒
            marks.append({
                "t": chunk["offset"] / 1e7,
                "d": chunk["duration"] / 1e7,
                "w": chunk["text"],
            })
    return bytes(audio), marks

_cache: dict = {}   # (text, voice, rate) -> (audio, marks)，避免重复合成

@app.get("/api/voices")
async def api_voices():
    return VOICE_LIST

@app.post("/api/tts")
async def api_tts(req: TTSReq):
    voice = req.voice or VOICES.get((req.accent, req.gender), VOICES[("en-US", "female")])
    key = (req.text, voice, round(req.rate, 2))
    if key in _cache:
        audio, marks = _cache[key]
    else:
        audio, marks = await synth(req.text, voice, req.rate)
        if len(_cache) > 800:
            _cache.pop(next(iter(_cache)))
        _cache[key] = (audio, marks)
    return {
        "audio_b64": base64.b64encode(audio).decode("ascii"),
        "mime": "audio/mpeg",
        "marks": marks,
    }

# ===================== 视频提取 =====================
class ExtractReq(BaseModel):
    url: str
    start: Optional[float] = None   # 秒
    end: Optional[float] = None
    vocals: bool = False            # 去除背景音（Demucs 人声分离）
    source_language: SourceLanguage = "en"

@app.post("/api/extract")
def api_extract(req: ExtractReq):    # 用同步函数，FastAPI 自动丢线程池（yt-dlp/whisper 是阻塞的）
    try:
        return extract_media(
            req.url,
            req.start,
            req.end,
            req.vocals,
            req.source_language,
        )
    except Exception as ex:
        return JSONResponse(status_code=400, content={"error": str(ex)})

# ---------- Azure 发音评测 ----------
AZURE_KEY_FILE = BASE / "azure_key.txt"
AZURE_ENV_KEY = os.environ.get("AZURE_SPEECH_KEY")   # 环境变量优先；设了就锁定，面板不覆盖
AZURE_KEY = AZURE_ENV_KEY
AZURE_REGION = os.environ.get("AZURE_SPEECH_REGION", "eastasia")

def _load_azure_from_file():
    """环境变量没设时，从项目根 azure_key.txt 读 key/region 到全局（第1行=key，可选第2行=region）。
    该文件含密钥，勿外发/提交 git。设置面板保存后会调它即时生效，无需重启。"""
    global AZURE_KEY, AZURE_REGION
    if AZURE_ENV_KEY:
        return
    if AZURE_KEY_FILE.exists():
        lines = [l.strip() for l in AZURE_KEY_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
        if lines:
            AZURE_KEY = lines[0]
            if len(lines) > 1:
                AZURE_REGION = lines[1]

_load_azure_from_file()


def _to_wav16k(raw: bytes, suffix: str) -> str:
    """把上传的音频(webm/ogg/wav…)用 ffmpeg 转成 Azure 要的 16k 单声道 16bit PCM wav，返回临时文件路径。"""
    input_path = ""
    output_path = ""
    transcode_succeeded = False
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix or ".bin", delete=False) as fin:
            input_path = fin.name
            fin.write(raw)
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fout:
            output_path = fout.name
        subprocess.run(
            [
                "ffmpeg", "-y", "-i", input_path,
                "-ar", "16000", "-ac", "1", "-f", "wav", output_path,
            ],
            check=True,
            capture_output=True,
        )
        transcode_succeeded = True
        return output_path
    finally:
        if input_path:
            try:
                os.unlink(input_path)
            except OSError:
                pass
        if output_path and not transcode_succeeded:
            try:
                os.unlink(output_path)
            except OSError:
                pass


def _assess(wav_path: str, reference_text: str, language: str) -> dict:
    """对 wav 跑 Azure 发音评测（音素粒度 + 漏读多读 + 韵律），返回结构化结果。阻塞调用，放线程池里跑。"""
    import azure.cognitiveservices.speech as speechsdk
    speech_config = speechsdk.SpeechConfig(subscription=AZURE_KEY, region=AZURE_REGION)
    audio_config = speechsdk.audio.AudioConfig(filename=wav_path)
    pa = speechsdk.PronunciationAssessmentConfig(
        reference_text=reference_text,
        grading_system=speechsdk.PronunciationAssessmentGradingSystem.HundredMark,
        granularity=speechsdk.PronunciationAssessmentGranularity.Phoneme,
        enable_miscue=True,
    )
    try:
        pa.enable_prosody_assessment()   # 韵律（语调/重音），个别区域不支持就跳过
    except Exception:
        pass
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=speech_config, language=language, audio_config=audio_config)
    pa.apply_to(recognizer)
    result = recognizer.recognize_once()

    if result.reason == speechsdk.ResultReason.NoMatch:
        return {"ok": False, "error": "no_speech", "message": "没听到清晰的语音，请靠近麦克风重读。"}
    if result.reason == speechsdk.ResultReason.Canceled:
        det = result.cancellation_details
        return {"ok": False, "error": "canceled",
                "message": f"Azure 取消：{det.reason} {det.error_details or ''}".strip()}

    r = speechsdk.PronunciationAssessmentResult(result)
    words = []
    for w in (r.words or []):
        words.append({
            "word": w.word,
            "accuracy": round(w.accuracy_score, 1) if w.accuracy_score is not None else None,
            "error": w.error_type,   # None / Mispronunciation / Omission / Insertion
            "phonemes": [{"p": ph.phoneme, "score": round(ph.accuracy_score, 1)}
                         for ph in (w.phonemes or [])],
        })
    return {
        "ok": True,
        "recognized": result.text,
        "scores": {
            "pronunciation": round(r.pronunciation_score, 1) if r.pronunciation_score is not None else None,
            "accuracy": round(r.accuracy_score, 1) if r.accuracy_score is not None else None,
            "fluency": round(r.fluency_score, 1) if r.fluency_score is not None else None,
            "completeness": round(r.completeness_score, 1) if r.completeness_score is not None else None,
            "prosody": round(r.prosody_score, 1) if getattr(r, "prosody_score", None) is not None else None,
        },
        "words": words,
    }


@app.post("/api/assess")
async def api_assess(audio: UploadFile = File(...),
                     reference_text: str = Form(...),
                     accent: str = Form("en-US")):
    if not AZURE_KEY:
        return JSONResponse(status_code=503, content={
            "ok": False, "error": "not_configured",
            "message": "未配置 Azure 发音评测。设环境变量 AZURE_SPEECH_KEY / AZURE_SPEECH_REGION 后重启即可。"})
    raw = await audio.read()
    if not raw:
        return JSONResponse(status_code=400, content={"ok": False, "error": "empty", "message": "没收到音频。"})
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    language = accent if accent in ("en-US", "en-GB") else "en-US"
    wav = None
    try:
        wav = await run_in_threadpool(_to_wav16k, raw, suffix)
        return await run_in_threadpool(_assess, wav, reference_text, language)
    except subprocess.CalledProcessError as ex:
        return JSONResponse(status_code=400, content={
            "ok": False, "error": "transcode", "message": "音频转码失败：" + (ex.stderr or b"").decode("utf-8", "ignore")[-300:]})
    except Exception as ex:
        return JSONResponse(status_code=500, content={"ok": False, "error": "assess", "message": str(ex)})
    finally:
        if wav:
            try: os.unlink(wav)
            except OSError: pass


# ---------- Azure key 设置面板（App 内填 key，存了即时生效、无需重启）----------
@app.get("/api/config/azure")
async def get_azure_config():
    tail = AZURE_KEY[-4:] if (AZURE_KEY and len(AZURE_KEY) >= 4) else ""
    return {"configured": bool(AZURE_KEY), "region": AZURE_REGION,
            "key_tail": tail, "locked": bool(AZURE_ENV_KEY)}


@app.post("/api/config/azure")
async def set_azure_config(request: Request):
    """把 key/region 写进 azure_key.txt 并更新全局，立刻生效。环境变量配过的话拒绝覆盖。"""
    global AZURE_KEY, AZURE_REGION
    if AZURE_ENV_KEY:
        return JSONResponse(status_code=409, content={
            "ok": False, "message": "已用环境变量 AZURE_SPEECH_KEY 配置，设置面板不覆盖它。"})
    try:
        data = await request.json()
    except Exception:
        data = {}
    key = (data.get("key") or "").strip()
    region = (data.get("region") or "").strip() or "southeastasia"
    if not key:
        return JSONResponse(status_code=400, content={"ok": False, "message": "key 不能为空。"})
    try:
        AZURE_KEY_FILE.write_text(key + "\n" + region + "\n", encoding="utf-8")
    except OSError as ex:
        return JSONResponse(status_code=500, content={"ok": False, "message": "写入 azure_key.txt 失败：" + str(ex)})
    AZURE_KEY = key
    AZURE_REGION = region
    return {"ok": True, "configured": True, "region": region, "key_tail": key[-4:] if len(key) >= 4 else ""}


# ---------- 基础识别（本地 Whisper，免费离线；跟读没配 Azure 时的兜底，不依赖 Google）----------
WHISPER_MODEL = os.getenv("WHISPER_MODEL", "base.en")
_whisper = None
_whisper_init_lock = threading.Lock()


def _transcribe_text(wav_path: str) -> str:
    """对一小段录音做本地转写，返回整段文字（用于跟读"基础"引擎的文字匹配）。"""
    global _whisper
    if _whisper is None:
        with _whisper_init_lock:
            if _whisper is None:
                from faster_whisper import WhisperModel
                _whisper = WhisperModel(
                    WHISPER_MODEL,
                    device="cpu",
                    compute_type="int8",
                    cpu_threads=6,
                )
    segs, _ = _whisper.transcribe(wav_path, language="en", beam_size=1, condition_on_previous_text=False)
    return " ".join(s.text.strip() for s in segs).strip()


@app.post("/api/transcribe")
async def api_transcribe(audio: UploadFile = File(...)):
    raw = await audio.read()
    if not raw:
        return JSONResponse(status_code=400, content={"ok": False, "message": "没收到音频。"})
    suffix = os.path.splitext(audio.filename or "")[1] or ".webm"
    wav = None
    try:
        wav = await run_in_threadpool(_to_wav16k, raw, suffix)
        text = await run_in_threadpool(_transcribe_text, wav)
        return {"ok": True, "text": text}
    except subprocess.CalledProcessError as ex:
        return JSONResponse(status_code=400, content={
            "ok": False, "message": "音频转码失败：" + (ex.stderr or b"").decode("utf-8", "ignore")[-300:]})
    except Exception as ex:
        return JSONResponse(status_code=500, content={"ok": False, "message": "本地识别失败：" + str(ex)})
    finally:
        if wav:
            try: os.unlink(wav)
            except OSError: pass


# ---------- 上传 PDF / Word 提取文字 ----------
def _clean_doc_text(t: str) -> str:
    """整理提取出的文本：去 PDF 断词换行、段内换行合成空格、保留段落分隔（每段一行）。
    这样前端 splitSentences 只按句末标点切句，不会被 PDF 的行内换行错切。"""
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"-\n(\w)", r"\1", t)                 # 修 PDF 常见的断词换行 exam-\nple -> example
    out = []
    for para in re.split(r"\n\s*\n+", t):            # 空行分段
        line = " ".join(s.strip() for s in para.split("\n") if s.strip())
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        if line:
            out.append(line)
    return "\n".join(out)


def _extract_doc_text(raw: bytes, filename: str) -> str:
    import io
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(raw))
        return "\n\n".join((p.extract_text() or "") for p in reader.pages).strip()
    if ext == ".docx":
        import docx
        d = docx.Document(io.BytesIO(raw))
        return "\n".join(p.text for p in d.paragraphs if p.text.strip()).strip()
    if ext == ".doc":
        raise ValueError("doc_old")
    raise ValueError("unsupported")


@app.post("/api/extract-doc")
async def api_extract_doc(doc: UploadFile = File(...)):
    raw = await doc.read()
    if not raw:
        return JSONResponse(status_code=400, content={"ok": False, "message": "没收到文件。"})
    name = doc.filename or ""
    ext = os.path.splitext(name)[1].lower()
    try:
        text = await run_in_threadpool(_extract_doc_text, raw, name)
    except ValueError as ve:
        msg = ("旧版 .doc 暂不支持，请用 Word 另存为 .docx 或导出 PDF 再上传。"
               if str(ve) == "doc_old" else "只支持 PDF 和 .docx 文件。")
        return JSONResponse(status_code=400, content={"ok": False, "message": msg})
    except Exception as ex:
        return JSONResponse(status_code=500, content={"ok": False, "message": "解析失败：" + str(ex)})
    text = _clean_doc_text(text)
    if not text:
        msg = ("这个 PDF 没有可提取的文字（多半是扫描/图片版），需要 OCR 才能识别，暂未支持。"
               if ext == ".pdf" else "文档里没提取到文字。")
        return JSONResponse(status_code=400, content={"ok": False, "message": msg})
    return {"ok": True, "text": text, "chars": len(text)}


# ---------- 盲听免空格：按真实英文词切（答案词优先消歧）----------
# 和"把字母对齐到答案"无关：用词频做动态规划断词，歧义处偏向答案里出现过的词，
# 所以打对的能拿全分，听错的也切成可读真词，不会把 long day 毁成 longd ay。
_WS_READY = False
_WS_TOTAL = 1

def _ws_prepare():
    global _WS_READY, _WS_TOTAL
    if not _WS_READY:
        from wordsegment import load as _load, UNIGRAMS
        _load()
        _WS_TOTAL = sum(UNIGRAMS.values()) or 1
        _WS_READY = True


def word_break(text: str, answer: str, maxlen: int = 20):
    from wordsegment import UNIGRAMS
    _ws_prepare()
    s = "".join(c for c in text.lower() if c.isalpha())   # 只留字母：忽略用户打的空格/标点（含手误的空格）
    if not s:
        return []
    ans = {}
    for tok in re.findall(r"[a-z']+", answer.lower()):
        ans[tok.replace("'", "")] = tok                   # 去撇号做键，值=原词（带撇号，回显时还原，如 youre->you're）

    def cost(w: str) -> float:
        if w in ans:
            return 0.5                                    # 答案里的词：强烈偏向，消歧
        c = UNIGRAMS.get(w)
        if c:
            return -math.log10(c / _WS_TOTAL)             # 常见词：词频越高越便宜
        return 12 + len(w)                                # 未知词重罚：逼着切成已知词

    n = len(s)
    best = [0.0] + [float("inf")] * n
    back = [0] * (n + 1)
    for i in range(1, n + 1):
        for j in range(max(0, i - maxlen), i):
            v = best[j] + cost(s[j:i])
            if v < best[i]:
                best[i] = v
                back[i] = j
    out, i = [], n
    while i > 0:
        j = back[i]
        w = s[j:i]
        out.append(ans.get(w, w))
        i = j
    return out[::-1]


class SegReq(BaseModel):
    text: str
    answer: str = ""


@app.post("/api/segment")
async def api_segment(req: SegReq):
    words = await run_in_threadpool(word_break, req.text, req.answer)
    return {"words": words}


# ---------- 音标：本地 CMU 词典→IPA（离线、覆盖广、不依赖国外词典 API）----------
_e2ipa = None
@app.get("/api/ipa")
async def api_ipa(word: str = ""):
    """查单词音标。本地 eng_to_ipa（含 CMU 词典，规则/不规则/人名/缩写都覆盖）。
    查不到的生僻/合成词返回空串，前端再退到在线词典兜底。"""
    global _e2ipa
    w = (word or "").strip()
    if not w:
        return {"ipa": ""}
    try:
        if _e2ipa is None:
            import eng_to_ipa as _e2ipa
        res = _e2ipa.convert(w)
    except Exception:
        return {"ipa": ""}
    ok = bool(res) and "*" not in res and res.strip().lower() != w.lower()
    return {"ipa": res if ok else ""}


@app.get("/api/health")
async def health():
    return {"ok": True, "azure": bool(AZURE_KEY), "region": AZURE_REGION}

# 首页 + 静态资源（放最后，避免盖住上面的 /api 路由）
@app.get("/")
async def index():
    return FileResponse(BASE / "index.html")

app.mount("/", StaticFiles(directory=str(BASE)), name="static")
