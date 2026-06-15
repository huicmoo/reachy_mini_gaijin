#!/usr/bin/env python3
"""
dun_conversation.py — 墩墩语音对话系统 (v7)
Pi 5 + USB 音频模块(SSS1629A5) + Whisper + Ollama + edge-tts + ESP32 眼睛

v7 改动 (2026-06-15):
  - 修复眼睛状态：待机用 AWAIT（活跃等待动画），不再用 NORMAL（静止/关闭）
  - _speak() 播完回 AWAIT，不是 NORMAL
  - 唤醒检测非目标词后回 AWAIT，不是 NORMAL
  - 程序启动：NORMAL → AWAIT；程序退出：NORMAL
  - 修复活跃模式循环：活跃期间超时后正确回到待机
"""

import os
import sys
os.environ['HF_HUB_OFFLINE'] = '1'  # 禁止联网下载，用本地缓存

import time
import subprocess
import tempfile
import asyncio
import logging
from datetime import datetime

import numpy as np
import sounddevice as sd
import requests

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

WHISPER_MODEL = "tiny"
WHISPER_LOCAL_PATH = os.path.expanduser("~/.cache/huggingface/hub/models--Systran--faster-whisper-tiny/snapshots")
OLLAMA_MODEL = "qwen2:1.5b"
OLLAMA_URL = "http://localhost:11434/api/generate"

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
ENERGY_THRESHOLD = 0.03
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
        return  # 避免重复发送相同状态
    _current_eye = state
    log.info(f"👀 → {state}")
    if _eye_serial and _eye_serial.is_open:
        try:
            _eye_serial.write(f"{state}\n".encode())
            _eye_serial.flush()
        except Exception:
            pass


# ─────────────────────────── TTS ───────────────────────────
def _speak(text: str):
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
        _set_eye("AWAIT")  # 播完回到活跃等待，不是 NORMAL


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


def _record(seconds: float) -> np.ndarray:
    n_samples = int(seconds * NATIVE_RATE)
    sd.default.device = AUDIO_DEVICE
    rec = sd.rec(n_samples, samplerate=NATIVE_RATE, channels=NATIVE_CHANS, dtype='float32')
    sd.wait()
    ch0, ch1 = rec[:, 0], rec[:, 1]
    best = ch0 if np.sqrt(np.mean(ch0**2)) >= np.sqrt(np.mean(ch1**2)) else ch1
    return _resample(best, NATIVE_RATE, TARGET_RATE)


def _normalize(audio: np.ndarray, target_peak: float = 0.7) -> np.ndarray:
    audio = audio.copy()
    audio[np.abs(audio) < NOISE_GATE] = 0.0
    peak = np.abs(audio).max()
    if peak > 0.01:
        return np.clip(audio * min(target_peak / peak, 15.0), -1.0, 1.0)
    return audio


def _has_speech(audio: np.ndarray) -> bool:
    return float(np.abs(audio).max()) > ENERGY_THRESHOLD


# ─────────────────────────── ASR ───────────────────────────
_whisper_model = None


def _init_whisper():
    global _whisper_model
    from faster_whisper import WhisperModel
    log.info("🧠 加载 Whisper...")
    _whisper_model = WhisperModel(WHISPER_MODEL, device="cpu", compute_type="int8")
    log.info("🧠 Whisper 就绪")


def _transcribe(audio: np.ndarray, prompt: str = "") -> str:
    kwargs = {'language': 'zh', 'beam_size': 1}
    if prompt:
        kwargs['initial_prompt'] = prompt
    segs, _ = _whisper_model.transcribe(audio.astype(np.float32), **kwargs)
    return ''.join(s.text for s in segs).strip()


def _is_wake_word(text: str) -> bool:
    t = text.strip().lower()
    if any(w in t for w in WAKE_WORDS_EXACT):
        return True
    return any(s in t for s in WAKE_SOUNDS)


# ─────────────────────────── 唤醒检测 ───────────────────────────
def _whisper_wake_listen():
    """待机模式：循环录音检测唤醒词。检测到唤醒词返回 True，否则继续循环。"""
    while True:
        audio = _record(1.5)

        if not _has_speech(audio):
            continue

        log.info("🗣️ VAD 检测到人声，继续录音...")
        _set_eye("LISTENING")
        extra = _record(2.0)

        combined = _normalize(np.concatenate([audio, extra]))

        log.info("🧠 识别中...")
        _set_eye("THINKING")
        text = _transcribe(combined, prompt="墩墩")
        log.info(f"👂 听到：「{text}」")

        if _is_wake_word(text):
            return True

        log.info("❌ 非目标唤醒词，继续监听")
        _set_eye("AWAIT")  # 回到活跃等待，不是 NORMAL


# ─────────────────────────── 问题录制（活跃模式）─────────────────
def _listen_for_question() -> str:
    """活跃模式：录制用户问题。有语音返回识别文本，无语音返回空字符串。"""
    for attempt in range(3):
        log.info(f"🎤 请说你的问题...（{attempt+1}/3）")
        _set_eye("LISTENING")
        audio = _record(4.0)

        if not _has_speech(audio):
            log.info("（无人声，继续等待）")
            continue

        audio = _normalize(audio)
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
    _set_eye("NORMAL")   # 启动先静止
    _init_whisper()
    _set_eye("AWAIT")    # 加载完切到活跃等待
    log.info(f"🎙️ 录音设备: {AUDIO_DEVICE} | {NATIVE_RATE}Hz {NATIVE_CHANS}ch → {TARGET_RATE}Hz")

    active_until = 0.0

    while True:
        now = time.time()
        in_active_mode = (now < active_until)

        if not in_active_mode:
            log.info('💤 [待机] 等待唤醒...（说"墩墩"）')
            _set_eye("AWAIT")  # 待机用 AWAIT（活跃等待动画），不是 NORMAL

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
