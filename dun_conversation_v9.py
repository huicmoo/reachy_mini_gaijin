#!/usr/bin/env python3
"""
dun_conversation.py — 墩墩语音对话系统 (v9.1)
Pi 5 + USB 音频模块(SSS1629A5) + SenseVoice + Ollama + edge-tts + ESP32 眼睛

v9.1 改动 (2026-06-15):
  - 🆕 ASR 引擎从 faster-whisper 替换为 sherpa-onnx + SenseVoiceSmall
  - 🆕 中文识别准确率大幅提升（"你好"再不会识别成"你不受就好"）
  - 🆕 SenseVoice int8 量化版，Pi 5 CPU 实时性好
  - 🆕 sherpa-onnx 装不上/模型找不到时自动回退 faster-whisper
  - 🆕 去掉 SenseVoice 输出中的特殊 tag（<|zh|><|NEUTRAL|>...）

v8 改动 (2026-06-15):
  - 播放TTS时暂停麦克风（防回声回灌）
  - webrtcvad 智能VAD（替换简单能量阈值）
  - 智能录音：VAD检测人声起止，说完就停，不录噪音尾巴
  - 保留能量VAD作为fallback（无需额外依赖也能跑）
  - 保留v7所有修复（眼睛状态、HF离线模式等）
"""

import os
import sys
os.environ['HF_HUB_OFFLINE'] = '1'  # 禁止联网下载，用本地缓存

import time
import subprocess
import tempfile
import asyncio
import logging
import collections
from datetime import datetime

import numpy as np
import sounddevice as sd
import requests

# ─── 尝试导入 webrtcvad ───
try:
    import webrtcvad
    _HAS_WEBRTC = True
except ImportError:
    _HAS_WEBRTC = False

# ─────────────────────────── 配置区 ───────────────────────────
SERIAL_PORT = "/dev/ttyACM0"


def _find_usb_mic():
    devs = sd.query_devices()
    for d in devs:
        if 'USB' in d['name'] and d['max_input_channels'] > 0:
            return d['name']
    return None


AUDIO_DEVICE = _find_usb_mic()
NATIVE_RATE = 44100
NATIVE_CHANS = 2
TARGET_RATE = 16000

WHISPER_MODEL = "small"
WHISPER_LOCAL_PATH = os.path.expanduser("~/.cache/huggingface/hub/models--Systran--faster-whisper-small/snapshots")

# SenseVoice 配置（v9.1 新增）
SENSEVOICE_MODEL_DIR = os.path.expanduser(
    "~/reachy_mini_gaijin/models/sensevoice/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17"
)
SENSEVOICE_USE_INT8 = True  # 用 int8 量化版（229MB），Pi 5 实时性好
SHERPA_FALLBACK_TO_FASTER_WHISPER = True  # sherpa-onnx 装不上/模型找不到时自动退回
OLLAMA_MODEL = "qwen2:1.5b"
OLLAMA_URL = "http://localhost:11434/api/generate"

# ── VAD 配置 ──
VAD_AGGRESSIVENESS = 3    # webrtcvad 过滤力度 0-3 (3=最严格)
VAD_FRAME_MS = 30         # VAD 帧长度 (ms)，webrtcvad 支持 10/20/30
VAD_CHUNK_MS = 200        # 录音块大小 (ms)
ENERGY_THRESHOLD = 0.03   # 能量VAD fallback 阈值

# ── 智能录音配置 ──
WAKE_SPEECH_TIMEOUT = 4.0   # 唤醒词录音：最长录几秒
WAKE_SILENCE_LIMIT = 0.8    # 唤醒词：静音多久算说完
QUESTION_TIMEOUT = 8.0      # 问题录音：最长录几秒
QUESTION_SILENCE_LIMIT = 1.2 # 问题：静音多久算说完
NO_SPEECH_TIMEOUT = 5.0     # 等人开口说话最长几秒（问题模式用）

WAKE_WORDS_EXACT = {
    '墩墩', '顿顿', '吨吨',
    '噴噴', '喷喷',
    '蹲蹲',
    '登登', '等灯',
}
WAKE_SOUNDS = {
    'dun', 'tun', 'duan', 'dong', 'ding', 'deng', 'den',
    'pen', 'pun', 'zhun',
    'pen pen', 'pun pun',
}

ACTIVE_TIMEOUT = 120
NOISE_GATE = 0.005

SYSTEM_PROMPT = (
    "你是墩墩，一个可爱的小机器人。"
    "你的名字叫墩墩，不是Qwen。"
    "回答要简短有趣，不超过50字。"
    "用简体中文。"
)

# ── 日志格式 ──
class EmojiFormatter(logging.Formatter):
    def format(self, record):
        msg = record.getMessage()
        return f'{time.strftime("%H:%M:%S")} {msg}'

log = logging.getLogger('dundun')
log.setLevel(logging.INFO)
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(EmojiFormatter())
log.handlers = [handler]

# ─────────────────────────── 麦克风静音 ───────────────────────────
_mic_muted = False


# ─────────────────────────── 眼睛控制 ───────────────────────────
_eye_serial = None
_current_eye = "NORMAL"


def _init_eye():
    global _eye_serial
    try:
        import serial
        _eye_serial = serial.Serial(SERIAL_PORT, 115200, timeout=1)
        time.sleep(0.5)
        log.info("👀 眼睛串口已连接")
    except Exception as e:
        log.warning(f"👀 眼睛串口不可用（{e}），继续无眼睛模式")
        _eye_serial = None


def _set_eye(state: str):
    global _current_eye
    if _current_eye == state:
        return
    _current_eye = state
    log.info(f"👀 → {state}")
    if _eye_serial and _eye_serial.is_open:
        try:
            _eye_serial.write(f"{state}\n".encode())
            _eye_serial.flush()
        except Exception:
            pass


# ─────────────────────────── TTS（带麦克风静音）───────────────────
def _speak(text: str):
    global _mic_muted
    _mic_muted = True   # ← 播放前静音，防回声
    _set_eye("RESPONSE")
    try:
        import edge_tts

        async def _gen():
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
                mp3_path = f.name
            communicate = edge_tts.Communicate(text, voice="zh-CN-XiaoxiaoNeural")
            await communicate.save(mp3_path)
            return mp3_path

        mp3_path = asyncio.run(_gen())
        wav_path = mp3_path.replace('.mp3', '.wav')
        subprocess.run(['ffmpeg', '-y', '-i', mp3_path, wav_path], capture_output=True)
        subprocess.run(['aplay', '-q', wav_path])
        os.unlink(mp3_path)
        os.unlink(wav_path)
    except Exception as e:
        log.error(f"TTS 失败: {e}")
    finally:
        _mic_muted = False  # ← 播完恢复
        _set_eye("AWAIT")


# ─────────────────────────── 快速回答 ───────────────────────────
def _quick_answer(text: str):
    now = datetime.now()
    t = text.lower()
    if any(w in t for w in ['几点', '时间', '现在几', 'what time']):
        return f"现在是{now.strftime('%H点%M分')}。"
    if any(w in t for w in ['几号', '日期', '今天', 'today', 'date']):
        week = ['一', '二', '三', '四', '五', '六', '日'][now.weekday()]
        return f"今天是{now.month}月{now.day}日，星期{week}。"
    return None


# ─────────────────────────── LLM ───────────────────────────
def _ask_ollama(text: str) -> str:
    now = datetime.now()
    date_str = now.strftime('%Y年%m月%d日 %H:%M')
    prompt = f"[当前时间: {date_str}]\n用户: {text}"
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "system": SYSTEM_PROMPT,
            "stream": False,
            "options": {"temperature": 0.7, "num_predict": 80}
        }, timeout=30)
        return resp.json().get('response', '抱歉，我没想好怎么回答。').strip()
    except Exception as e:
        log.error(f"Ollama 失败: {e}")
        return "抱歉，我的大脑暂时转不动了。"


# ─────────────────────────── 音频工具 ───────────────────────────
def _resample(audio_f32: np.ndarray, orig_rate: int, target_rate: int) -> np.ndarray:
    if orig_rate == target_rate:
        return audio_f32
    n_target = int(len(audio_f32) * target_rate / orig_rate)
    x_old = np.linspace(0, 1, len(audio_f32))
    x_new = np.linspace(0, 1, n_target)
    return np.interp(x_new, x_old, audio_f32)


def _normalize(audio: np.ndarray, target_peak: float = 0.7) -> np.ndarray:
    audio = audio.copy()
    audio[np.abs(audio) < NOISE_GATE] = 0.0
    peak = np.abs(audio).max()
    if peak > 0.01:
        return np.clip(audio * min(target_peak / peak, 15.0), -1.0, 1.0)
    return audio


# ─────────────────────────── VAD ───────────────────────────
_vad = None


def _init_vad():
    global _vad
    if _HAS_WEBRTC:
        _vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
        log.info(f"🔊 VAD: webrtcvad (级别={VAD_AGGRESSIVENESS})")
    else:
        _vad = None
        log.info("🔊 VAD: 能量阈值（建议 pip install webrtcvad）")


def _chunk_has_speech(chunk_16k: np.ndarray) -> bool:
    """检测 16kHz float32 音频块是否包含人声"""
    if _vad is not None:
        # webrtcvad: 需要 int16, 固定帧长
        chunk_i16 = (chunk_16k * 32767).astype(np.int16)
        frame_size = int(TARGET_RATE * VAD_FRAME_MS / 1000)  # 480 samples
        n_frames = len(chunk_i16) // frame_size
        if n_frames == 0:
            return False
        speech_count = 0
        for i in range(n_frames):
            frame = chunk_i16[i * frame_size:(i + 1) * frame_size]
            if _vad.is_speech(frame.tobytes(), TARGET_RATE):
                speech_count += 1
        # 超过 30% 的帧有人声就算有人声
        return speech_count > n_frames * 0.3
    else:
        # Fallback: 能量阈值
        return float(np.abs(chunk_16k).max()) > ENERGY_THRESHOLD


# ─────────────────────────── 智能录音 ───────────────────────────
def _record_chunk(chunk_ms: float) -> np.ndarray:
    """录一段音频，返回 16kHz float32 单声道"""
    n_native = int(NATIVE_RATE * chunk_ms / 1000)
    sd.default.device = AUDIO_DEVICE
    rec = sd.rec(n_native, samplerate=NATIVE_RATE, channels=NATIVE_CHANS, dtype='float32')
    sd.wait()
    ch0, ch1 = rec[:, 0], rec[:, 1]
    best_ch = ch0 if np.sqrt(np.mean(ch0**2)) >= np.sqrt(np.mean(ch1**2)) else ch1
    return _resample(best_ch, NATIVE_RATE, TARGET_RATE)


def _record_smart(timeout: float = 8.0, silence_limit: float = 1.0,
                  wait_speech: bool = True) -> np.ndarray:
    """
    智能录音（VAD驱动）：
    1. 先等人开口（wait_speech=True 时）
    2. 检测到人声开始录
    3. 人声结束后等待 silence_limit 秒
    4. 如果还是安静，录音结束
    5. 返回归一化后的 16kHz 音频，无人声返回空数组

    Args:
        timeout: 最长录音时长（秒）
        silence_limit: 说话结束后静音多久停止录音
        wait_speech: True=等人开口才录（问题模式）;
                     False=持续录不管有没有人声（唤醒模式用的循环）
    """
    chunk_ms = VAD_CHUNK_MS
    # 语音前缓冲：保留人声开始前 ~1s 的音频，避免截断
    pre_buffer = collections.deque(maxlen=max(1, int(1000 / chunk_ms)))

    audio_chunks = []
    is_speaking = False
    silence_start = None
    start_time = time.time()
    heard_any_speech = False

    while True:
        if _mic_muted:
            time.sleep(0.1)
            continue

        elapsed = time.time() - start_time
        if elapsed > timeout:
            if is_speaking:
                log.info("⏰ 录音超时（截断）")
            break

        chunk = _record_chunk(chunk_ms)
        has_speech = _chunk_has_speech(chunk)

        if has_speech:
            if not is_speaking:
                log.info("🗣️ 检测到人声开始")
                is_speaking = True
                heard_any_speech = True
                # 加入语音前缓冲（避免丢失开头）
                for pre in pre_buffer:
                    audio_chunks.append(pre)
                pre_buffer.clear()
            silence_start = None
            audio_chunks.append(chunk)
        else:
            if is_speaking:
                # 说话中间的短暂停顿，保留
                audio_chunks.append(chunk)
                if silence_start is None:
                    silence_start = time.time()
                elif time.time() - silence_start >= silence_limit:
                    log.info(f"🤫 静音 {silence_limit}s，录音结束")
                    break
            else:
                # 还没开始说话
                if wait_speech:
                    pre_buffer.append(chunk)
                    if elapsed > NO_SPEECH_TIMEOUT:
                        log.info("⏰ 等待超时，无人说话")
                        return np.array([], dtype=np.float32)
                else:
                    # 唤醒模式：不等人，持续收集
                    pre_buffer.append(chunk)

    if not audio_chunks:
        return np.array([], dtype=np.float32)

    result = np.concatenate(audio_chunks)
    log.info(f"📏 录音时长: {len(result)/TARGET_RATE:.1f}s")
    return _normalize(result)


# ─────────────────────────── ASR ───────────────────────────
_asr_engine = None  # "sensevoice" 或 "faster_whisper"
_asr_model = None


def _init_asr():
    """初始化 ASR：优先 SenseVoice（中文强），失败回退 faster-whisper"""
    global _asr_engine, _asr_model

    # 1) 尝试 SenseVoice（sherpa-onnx）
    int8_name = "model.int8.onnx" if SENSEVOICE_USE_INT8 else "model.onnx"
    model_path = os.path.join(SENSEVOICE_MODEL_DIR, int8_name)
    tokens_path = os.path.join(SENSEVOICE_MODEL_DIR, "tokens.txt")

    if os.path.exists(model_path) and os.path.exists(tokens_path):
        try:
            import sherpa_onnx
            log.info(f"🧠 加载 SenseVoice（{'int8' if SENSEVOICE_USE_INT8 else 'fp32'}）...")
            _asr_model = sherpa_onnx.OfflineRecognizer.from_sense_voice(
                model=model_path,
                tokens=tokens_path,
                num_threads=4,  # Pi 5 是 4 核
                use_itn=True,    # 逆文本规范化（数字、标点自动加）
                debug=False,
            )
            _asr_engine = "sensevoice"
            log.info("🧠 SenseVoice 就绪")
            return
        except Exception as e:
            log.warning(f"SenseVoice 初始化失败: {e}")

    # 2) 回退 faster-whisper
    if SHERPA_FALLBACK_TO_FASTER_WHISPER:
        try:
            from faster_whisper import WhisperModel
            log.info(f"🧠 加载 faster-whisper {WHISPER_MODEL}...")
            _asr_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
            _asr_engine = "faster_whisper"
            log.info("🧠 faster-whisper 就绪")
            return
        except Exception as e:
            log.error(f"faster-whisper 初始化也失败: {e}")

    raise RuntimeError("无可用 ASR 引擎！请装 sherpa-onnx 或 faster-whisper")


def _transcribe(audio: np.ndarray, prompt: str = "") -> str:
    """统一转写接口（SenseVoice 优先，自动回退 faster-whisper）"""
    if _asr_engine == "sensevoice":
        try:
            # SenseVoice：直接喂 16kHz float32 音频
            s = _asr_model.create_stream()
            s.accept_waveform(TARGET_RATE, (audio * 32767).astype(np.int16).tobytes())
            _asr_model.decode_stream(s)
            text = s.result.text.strip()
            # 去掉 SenseVoice 特殊 tag（如 <|zh|><|NEUTRAL|><|Speech|><|withitn|>）
            import re
            text = re.sub(r'<\|[^|]+\|>', '', text).strip()
            return text
        except Exception as e:
            log.warning(f"SenseVoice 识别失败，回退 faster-whisper: {e}")

    # faster-whisper 路径
    if _asr_engine == "faster_whisper" and _asr_model is not None:
        kwargs = {'language': 'zh', 'beam_size': 1}
        if prompt:
            kwargs['initial_prompt'] = prompt
        segs, _ = _asr_model.transcribe(audio.astype(np.float32), **kwargs)
        return ''.join(s.text for s in segs).strip()

    return ""


def _is_wake_word(text: str) -> bool:
    t = text.strip().lower()
    if any(w in t for w in WAKE_WORDS_EXACT):
        return True
    return any(s in t for s in WAKE_SOUNDS)


# ─────────────────────────── 唤醒检测 ───────────────────────────
def _whisper_wake_listen():
    """待机模式：用 VAD 循环检测唤醒词。"""
    while True:
        if _mic_muted:
            time.sleep(0.1)
            continue

        # 用 VAD 智能录音，等人说话
        _set_eye("AWAIT")
        audio = _record_smart(timeout=WAKE_SPEECH_TIMEOUT,
                              silence_limit=WAKE_SILENCE_LIMIT,
                              wait_speech=True)

        if len(audio) == 0:
            continue  # 没人说话，继续等

        log.info("🧠 识别中...")
        _set_eye("THINKING")
        text = _transcribe(audio, prompt="墩墩")
        log.info(f"👂 听到：「{text}」")

        if _is_wake_word(text):
            return True

        log.info("❌ 非目标唤醒词，继续监听")


# ─────────────────────────── 问题录制（活跃模式）─────────────────
def _listen_for_question() -> str:
    """活跃模式：用 VAD 智能录制用户问题。"""
    for attempt in range(3):
        log.info(f"🎤 请说你的问题...（{attempt+1}/3）")
        _set_eye("LISTENING")
        audio = _record_smart(timeout=QUESTION_TIMEOUT,
                              silence_limit=QUESTION_SILENCE_LIMIT,
                              wait_speech=True)

        if len(audio) == 0:
            log.info("（无人声，继续等待）")
            continue

        log.info("🧠 识别中...")
        _set_eye("THINKING")
        text = _transcribe(audio, prompt="")
        if text:
            return text

    return ""


# ─────────────────────────── 主循环 ───────────────────────────
def main():
    log.info("🤖 === 墩墩启动 ===")
    _init_eye()
    _set_eye("NORMAL")
    _init_vad()
    _init_asr()
    _set_eye("AWAIT")
    log.info(f"🎙️ 录音设备: {AUDIO_DEVICE} | {NATIVE_RATE}Hz {NATIVE_CHANS}ch → {TARGET_RATE}Hz")

    active_until = 0.0

    while True:
        now = time.time()
        in_active_mode = (now < active_until)

        if not in_active_mode:
            log.info('💤 [待机] 等待唤醒...（说"墩墩"）')

            if not _whisper_wake_listen():
                continue

            # ── 被唤醒 ──
            log.info("✨ === 墩墩被唤醒！===")
            _set_eye("AWAKEN")
            _speak("你好，我在！")
            active_until = time.time() + ACTIVE_TIMEOUT

            user_text = _listen_for_question()
            if not user_text:
                log.info("没听到问题，继续待机")
                continue

            log.info(f"❓ 问题: {user_text}")

        else:
            # ── 活跃模式 ──
            remaining = active_until - now
            log.info(f"💬 [活跃模式] 剩余 {remaining:.0f}s")

            user_text = _listen_for_question()
            if not user_text:
                log.info("没听到问题，继续等待")
                continue

            log.info(f"❓ 问题: {user_text}")

            if _is_wake_word(user_text):
                log.info("🔄 活跃模式再次唤醒，续期")
                active_until = time.time() + ACTIVE_TIMEOUT
                _speak("我在呢！")
                continue

        # ── 生成回答 ──
        answer = _quick_answer(user_text) or _ask_ollama(user_text)
        log.info(f"💬 回答: {answer}")
        _speak(answer)
        active_until = time.time() + ACTIVE_TIMEOUT


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n👋 [退出] 再见！")
        _set_eye("NORMAL")  # 退出时眼睛静止
