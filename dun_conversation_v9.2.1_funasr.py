#!/usr/bin/env python3
"""
墩墩对话系统 v9.2.1 - FunASR + SenseVoiceSmall（修复眼睛枚举 + 强制中文）
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

ASR_PRIMARY = 'funasr'
FUNASR_MODEL_ID = 'iic/SenseVoiceSmall'
ASR_FALLBACK = 'whisper'
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


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


# ==========================================
#                眼睛状态（关键修复）
# ==========================================
def init_eye():
    global _eye
    if not EYE_AVAILABLE:
        log('眼睛不可用')
        return
    try:
        _eye = EyeController()
        log('眼睛控制器初始化完成')
    except Exception as e:
        log(f'眼睛初始化失败: {e}')
        _eye = None


def set_eye_state(state):
    """设置眼睛状态 - 修复版：自动转 EyeState 枚举"""
    if _eye is None:
        return
    try:
        # 关键：把字符串转成 EyeState 枚举
        if isinstance(state, str) and EyeState is not None:
            # 尝试作为枚举名查找
            if hasattr(EyeState, state):
                state = getattr(EyeState, state)
            elif hasattr(EyeState, 'value'):
                # 如果 EyeState 是 str 枚举，直接传字符串
                pass
        _eye.set_state(state)
    except Exception as e:
        log(f'眼睛状态失败: {e}')


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
    """FunASR 识别 - 修复：强制中文 language='zh'"""
    import soundfile as sf
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        tmp_path = f.name
    try:
        sf.write(tmp_path, audio_16k, TARGET_RATE)
        result = _asr_model.generate(
            input=tmp_path,
            cache={},
            language='zh',         # 关键修复：强制中文，不让识别成韩文/英文
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
#                LLM / TTS
# ==========================================
def call_llm(prompt):
    try:
        r = ollama.chat(
            model=OLLAMA_MODEL,
            messages=[{'role': 'user', 'content': prompt}],
        )
        return r['message']['content'].strip()
    except Exception as e:
        log(f'LLM 错误: {e}')
        return '抱歉，我没听清。'


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
    log('  墩墩 v9.2.1 - FunASR + SenseVoiceSmall')
    log('  修复: EyeState枚举 + 强制中文')
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
            if any(w in text for w in WAKE_WORDS):
                _last_active_time = time.time()
                log('>>> 唤醒成功！')
                set_eye_state('AWAKEN')
                tts_play('你好，我在呢！')
                set_eye_state('AWAIT')
            else:
                set_eye_state('AWAIT')
        else:
            _last_active_time = time.time()
            set_eye_state('RESPONSE')
            response = call_llm(text)
            log(f'回复: {response[:80]}')
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
