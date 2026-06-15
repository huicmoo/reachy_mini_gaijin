#!/usr/bin/env python3
"""
墩墩对话系统 v9.3.1 - FunASR + SenseVoiceSmall
============================================
v9.3 → v9.3.1 改动：
  1. 眼睛状态诊断：打印 EyeState 实际类型 + set_state() 接收类型
  2. set_eye_state 加详细错误日志（不再是裸 except）
  3. 主动探查：初始化后立刻测一次状态切换，确认 ESP32 在线
"""

import os
os.environ['MODELSCOPE_CACHE'] = os.path.expanduser('~/reachy_mini_gaijin/models/funasr')
os.environ['HF_HUB_OFFLINE'] = '1'

import sys
import time
import re
import threading
import tempfile
import subprocess
import asyncio
import datetime
import numpy as np
import sounddevice as sd
import webrtcvad

import edge_tts
import ollama

# 眼睛控制
sys.path.insert(0, os.path.expanduser('~/reachy_mini_gaijin/src'))
try:
    from reachy_mini.io.eye_controller import EyeController, EyeState
    EYE_AVAILABLE = True
except Exception as e:
    print(f'[WARN] 眼睛控制器加载失败: {e}')
    EYE_AVAILABLE = False
    EyeState = None


# ==========================================
#                 配置
# ==========================================
SAMPLE_RATE_IN = 44100
CHANNELS = 2
TARGET_RATE = 16000
BLOCK_DURATION_MS = 30
BLOCK_SIZE_IN = int(SAMPLE_RATE_IN * BLOCK_DURATION_MS / 1000)

VAD_AGGRESSIVENESS = 2
PRE_BUFFER_DURATION = 1.0
SILENCE_DURATION = 1.2
MIN_SPEECH_DURATION = 0.3
MAX_RECORD_DURATION = 15

WAKE_WORDS = ['墩墩', '噴噴', '喷喷', '蹲蹲', '登登', '等灯', '顿顿', '吨吨']
ACTIVE_DURATION = 120

OLLAMA_MODEL = 'qwen2:1.5b'
TTS_VOICE = 'zh-CN-XiaoxiaoNeural'
TTS_RATE = '+5%'

# LLM system prompt - 钉死身份
SYSTEM_PROMPT = """你是墩墩，一个住在一台 reachy mini 桌面机器人上的小伙伴。
你的特点是：活泼、可爱、有点调皮，喜欢用"呀""呢""哦"等语气词。
回答要简短（1-2 句话），口语化，不要用"作为AI"这种生硬的表达。
当被问到名字时，回答"我叫墩墩"。"""

ASR_PRIMARY = 'funasr'
FUNASR_MODEL_ID = 'iic/SenseVoiceSmall'
WHISPER_MODEL_NAME = 'small'
WHISPER_COMPUTE_TYPE = 'int8'
WHISPER_DOWNLOAD_ROOT = os.path.expanduser('~/reachy_mini_gaijin/models/faster-whisper')

AUDIO_INPUT_DEVICE = None
AUDIO_OUTPUT_DEVICE = 'plughw:2,0'

_asr_model = None
_asr_engine_name = None
_mic_muted = threading.Event()
_last_active_time = 0
_eye = None
_chat_history = []  # 短期记忆（最近 6 轮）


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


# ==========================================
#                眼睛状态
# ==========================================
def init_eye():
    global _eye
    if not EYE_AVAILABLE:
        log('眼睛不可用')
        return
    # 1) 先杀掉可能占着串口的旧进程（防止 /dev/ttyACM0 被锁）
    try:
        import subprocess
        subprocess.run(['pkill', '-9', '-f', 'dun_conversation'], check=False)
        subprocess.run(['pkill', '-9', '-f', 'eye_controller'], check=False)
        time.sleep(0.3)
    except Exception:
        pass
    # 2) 检查串口
    try:
        import serial
        ser = serial.Serial('/dev/ttyACM0', 115200, timeout=0.5)
        ser.close()
        log('眼睛串口 /dev/ttyACM0 正常')
    except Exception as e:
        log(f'眼睛串口打开失败: {e}（试试拔插 USB）')
    # 3) 初始化控制器
    try:
        _eye = EyeController()
        # 打印 EyeState 实际类型，方便诊断
        log(f'眼睛控制器初始化完成')
        if EyeState is not None:
            sample = list(EyeState)[:3] if hasattr(EyeState, '__iter__') else [EyeState.AWAIT]
            log(f'EyeState 类型: {type(EyeState).__name__}, 成员示例: {sample}')
        # 4) 立刻试发一个 AWAIT，确认 ESP32 响应
        try:
            _eye.set_state('AWAIT')
            log('眼睛 set_state(AWAIT) 发送成功（眼睛应亮）')
        except Exception as e:
            log(f'眼睛 set_state(AWAIT) 失败: {e}')
    except Exception as e:
        log(f'眼睛初始化失败: {e}')
        _eye = None


def set_eye_state(state):
    """设置眼睛状态 - v9.3.1 兼容版（带详细错误）"""
    if _eye is None:
        return
    try:
        if isinstance(state, str) and EyeState is not None:
            # 情况1: EyeState 是 str 枚举（如 AWAIT = 'AWAIT'），直接传 str
            # 情况2: EyeState 是 IntEnum/普通 Enum，需要查表
            try:
                if hasattr(EyeState, state):
                    state = getattr(EyeState, state)
                # 如果是 str 枚举，state 保持 str
            except Exception as e:
                log(f'眼睛状态枚举转换失败: {state} -> {e}')
        _eye.set_state(state)
    except Exception as e:
        log(f'眼睛状态失败 ({state}): {type(e).__name__}: {e}')


# ==========================================
#                  ASR
# ==========================================
def _init_asr():
    global _asr_model, _asr_engine_name
    if ASR_PRIMARY == 'funasr':
        try:
            from funasr import AutoModel
            log(f'正在加载 FunASR + {FUNASR_MODEL_ID}...')
            _asr_model = AutoModel(
                model=FUNASR_MODEL_ID,
                trust_remote_code=True,
                device='cpu',
                disable_update=True,
            )
            _asr_engine_name = 'funasr'
            log('✓ FunASR 加载完成')
            return
        except Exception as e:
            log(f'⚠ FunASR 加载失败: {e}')

    from faster_whisper import WhisperModel
    log(f'加载 faster-whisper {WHISPER_MODEL_NAME}...')
    try:
        _asr_model = WhisperModel(
            WHISPER_MODEL_NAME,
            device='cpu',
            compute_type=WHISPER_COMPUTE_TYPE,
            download_root=WHISPER_DOWNLOAD_ROOT,
            local_files_only=True,
        )
        _asr_engine_name = 'whisper'
        log('✓ faster-whisper 加载完成')
    except Exception as e:
        log(f'✗ faster-whisper 加载失败: {e}')
        raise


def _transcribe(audio_16k):
    if _asr_engine_name == 'funasr':
        return _transcribe_funasr(audio_16k)
    return _transcribe_whisper(audio_16k)


def _transcribe_funasr(audio_16k):
    import soundfile as sf
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        tmp_path = f.name
    try:
        sf.write(tmp_path, audio_16k, TARGET_RATE)
        result = _asr_model.generate(
            input=tmp_path,
            cache={},
            language='zh',
            use_itn=True,
        )
        if result and len(result) > 0:
            text = result[0].get('text', '')
            text = re.sub(r'<\|[^|]+\|>', '', text).strip()
            return text
    except Exception as e:
        log(f'FunASR 识别失败: {e}')
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
    return ''


def _transcribe_whisper(audio_16k):
    try:
        segments, _ = _asr_model.transcribe(
            audio_16k,
            language='zh',
            vad_filter=False,
            beam_size=5,
        )
        return ' '.join(seg.text for seg in segments).strip()
    except Exception as e:
        log(f'faster-whisper 识别失败: {e}')
        return ''


# ==========================================
#              音频设备 + 降采样
# ==========================================
def find_input_device():
    if AUDIO_INPUT_DEVICE is not None:
        return AUDIO_INPUT_DEVICE
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if 'USB' in dev['name'] and dev['max_input_channels'] >= 2:
            log(f'→ USB 设备 [{i}] {dev["name"]}')
            return i
    return sd.default.device[0]


def downsample_block(stereo_int16):
    if stereo_int16.ndim == 1:
        mono = stereo_int16.astype(np.float32) / 32768.0
    elif stereo_int16.shape[1] == 2:
        l = stereo_int16[:, 0].astype(np.float32) / 32768.0
        r = stereo_int16[:, 1].astype(np.float32) / 32768.0
        mono = l if np.sqrt(np.mean(l**2)) > np.sqrt(np.mean(r**2)) else r
    else:
        mono = stereo_int16[:, 0].astype(np.float32) / 32768.0

    src_len = len(mono)
    dst_len = int(src_len * TARGET_RATE / SAMPLE_RATE_IN)
    if dst_len <= 1:
        return np.zeros(0, dtype=np.float32)
    return np.interp(np.linspace(0, src_len - 1, dst_len), np.arange(src_len), mono).astype(np.float32)


# ==========================================
#          VAD 智能录音器
# ==========================================
class VADRecorder:
    def __init__(self):
        try:
            self.vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
            self.vad_available = True
            log('webrtcvad VAD 已启用')
        except Exception:
            self.vad_available = False
            self.energy_threshold = 0.02

        self.pre_buffer = []
        self.speech_buffer = []
        self.is_speaking = False
        self.silence_start = None
        self.max_pre_blocks = int(PRE_BUFFER_DURATION * 1000 / BLOCK_DURATION_MS)
        self.max_record_blocks = int(MAX_RECORD_DURATION * 1000 / BLOCK_DURATION_MS)

    def _is_speech(self, block_16k):
        if self.vad_available:
            pcm = (block_16k * 32767).astype(np.int16).tobytes()
            try:
                return self.vad.is_speech(pcm, TARGET_RATE)
            except Exception:
                pass
        return np.sqrt(np.mean(block_16k ** 2)) > self.energy_threshold

    def feed(self, block_16k):
        is_speech = self._is_speech(block_16k)
        if is_speech:
            if not self.is_speaking:
                self.is_speaking = True
                self.speech_buffer = list(self.pre_buffer)
                self.silence_start = None
                log('  [VAD] 人声开始')
            self.speech_buffer.append(block_16k)
        else:
            if self.is_speaking:
                self.speech_buffer.append(block_16k)
                if self.silence_start is None:
                    self.silence_start = time.time()
                elif time.time() - self.silence_start >= SILENCE_DURATION:
                    return self._finish()
            else:
                self.pre_buffer.append(block_16k)
                if len(self.pre_buffer) > self.max_pre_blocks:
                    self.pre_buffer.pop(0)
        if self.is_speaking and len(self.speech_buffer) > self.max_record_blocks:
            return self._finish()
        return None

    def _finish(self):
        audio = np.concatenate(self.speech_buffer) if self.speech_buffer else np.array([], dtype=np.float32)
        duration = len(audio) / TARGET_RATE
        log(f'  [VAD] 录音结束 ({duration:.1f}s)')
        self.speech_buffer = []
        self.pre_buffer = []
        self.is_speaking = False
        self.silence_start = None
        if duration >= MIN_SPEECH_DURATION:
            return audio
        return None


# ==========================================
#         日期/名字 处理（不走 LLM）
# ==========================================
WEEKDAY_CN = ['星期一', '星期二', '星期三', '星期四', '星期五', '星期六', '星期日']


def try_local_answer(text):
    """如果问的是日期/名字这类固定问题，直接本地回答（不用 LLM，更快更准）"""
    t = text.lower().strip()
    now = datetime.datetime.now()

    # 名字相关
    if any(k in t for k in ['你叫什么', '你叫啥', '你的名字', '你名字', '叫什么名字']):
        return '我叫墩墩呀！'

    # 日期相关
    if any(k in t for k in ['今天几号', '今天多少号', '今天日期', '几号了', '今天多少']):
        return f'今天是 {now.year} 年 {now.month} 月 {now.day} 日，{WEEKDAY_CN[now.weekday()]}。'

    if any(k in t for k in ['今天星期几', '今天礼拜几', '今天周几', '星期几']):
        return f'今天是{WEEKDAY_CN[now.weekday()]}。'

    if any(k in t for k in ['几点了', '现在几点', '什么时间', '现在时间']):
        return f'现在 {now.hour} 点 {now.minute} 分。'

    return None  # 不匹配，走 LLM


# ==========================================
#                LLM / TTS
# ==========================================
def call_llm(prompt):
    """带短期记忆的 LLM 调用"""
    try:
        messages = [{'role': 'system', 'content': SYSTEM_PROMPT}]
        # 加入历史（最近 6 轮）
        for h in _chat_history[-6:]:
            messages.append(h)
        messages.append({'role': 'user', 'content': prompt})

        r = ollama.chat(model=OLLAMA_MODEL, messages=messages)
        text = r['message']['content'].strip()

        # 更新历史
        _chat_history.append({'role': 'user', 'content': prompt})
        _chat_history.append({'role': 'assistant', 'content': text})
        if len(_chat_history) > 12:  # 6 轮
            _chat_history[:] = _chat_history[-12:]

        return text
    except Exception as e:
        log(f'LLM 错误: {e}')
        return '哎呀，我脑子卡了一下，再说一遍？'


async def _tts_to_file(text, out_path):
    communicate = edge_tts.Communicate(text, voice=TTS_VOICE, rate=TTS_RATE)
    await communicate.save(out_path)


def tts_play(text):
    if not text.strip():
        return
    mp3_path = wav_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            mp3_path = f.name
        wav_path = mp3_path.replace('.mp3', '.wav')

        asyncio.run(_tts_to_file(text, mp3_path))
        subprocess.run(
            ['ffmpeg', '-y', '-i', mp3_path, '-ar', '44100', '-ac', '2', wav_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        _mic_muted.set()
        try:
            subprocess.run(['aplay', '-D', AUDIO_OUTPUT_DEVICE, wav_path],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        finally:
            _mic_muted.clear()
    except Exception as e:
        log(f'TTS 错误: {e}')
    finally:
        for p in [mp3_path, wav_path]:
            if p:
                try: os.unlink(p)
                except Exception: pass


# ==========================================
#                主循环
# ==========================================
def main():
    global _last_active_time
    log('=' * 50)
    log('  墩墩 v9.3.1 - FunASR + SenseVoiceSmall')
    log('  新增: 眼睛诊断日志 + 报日期 + 报名字 + 短期记忆')
    log('=' * 50)

    _init_asr()
    log(f'当前 ASR 引擎: {_asr_engine_name}')

    init_eye()
    set_eye_state('AWAIT')

    input_dev = find_input_device()
    recorder = VADRecorder()

    def audio_callback(indata, frames, time_info, status):
        global _last_active_time
        if _mic_muted.is_set():
            return
        block_16k = downsample_block(indata)
        if len(block_16k) == 0:
            return
        audio = recorder.feed(block_16k)
        if audio is None:
            return

        log(f'>>> 录音 {len(audio)/TARGET_RATE:.1f}s，开始识别...')
        set_eye_state('THINKING')
        text = _transcribe(audio)
        log(f'识别结果: {text!r}')

        if not text:
            set_eye_state('AWAIT')
            return

        active = (time.time() - _last_active_time) < ACTIVE_DURATION

        if not active:
            # 待机模式：等唤醒
            if any(w in text for w in WAKE_WORDS):
                _last_active_time = time.time()
                log('>>> 唤醒成功！')
                set_eye_state('AWAKEN')
                tts_play('我在呢！')
                set_eye_state('AWAIT')
            else:
                set_eye_state('AWAIT')
        else:
            # 激活模式：处理问题
            _last_active_time = time.time()

            # 先尝试本地回答
            local_answer = try_local_answer(text)

            set_eye_state('RESPONSE')
            if local_answer:
                log(f'本地回答: {local_answer}')
                tts_play(local_answer)
            else:
                response = call_llm(text)
                log(f'LLM 回复: {response[:80]}')
                tts_play(response)
            set_eye_state('AWAIT')

    log('>>> 开始监听...')
    set_eye_state('AWAIT')
    with sd.InputStream(
        device=input_dev,
        samplerate=SAMPLE_RATE_IN,
        channels=CHANNELS,
        dtype='int16',
        blocksize=BLOCK_SIZE_IN,
        callback=audio_callback,
    ):
        try:
            while True:
                time.sleep(0.1)
        except KeyboardInterrupt:
            log('>>> 退出')
            set_eye_state('NORMAL')


if __name__ == '__main__':
    main()
