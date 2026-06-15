#!/usr/bin/env python3
"""
墩墩对话系统 v9.2 - FunASR + SenseVoiceSmall
============================================
v9.1 改 v9.2 的原因:
  - sherpa-onnx 1.13.2 Python 绑定有 API bug，s.result.text 返回乱码
  - 改用阿里达摩院官方 FunASR SDK，专门为 SenseVoice 设计，API 稳定

特性:
  - ASR: FunASR + SenseVoiceSmall (int8, ~230MB) / fallback: faster-whisper small
  - VAD: webrtcvad 智能 VAD（人声起止检测，说完就停）
  - 防回声: TTS 播放时静音麦克风
  - 眼睛: ESP32-S3 LVGL 动态眼睛（串口控制）
  - LLM: Ollama qwen2:1.5b
  - TTS: edge-tts → ffmpeg → aplay

部署:
  1. pip install --break-system-packages funasr modelscope torchaudio
  2. scp 上传到 Pi 5
  3. 第一次跑会自动下载 SenseVoiceSmall 模型（230MB，5-10 分钟）
  4. 跑成功后说"墩墩，今天天气怎么样"测试
"""

import os
os.environ['MODELSCOPE_CACHE'] = os.path.expanduser('~/reachy_mini_gaijin/models/funasr')
os.environ['HF_HUB_OFFLINE'] = '1'  # 防止 HuggingFace 联网

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

# 第三方
import edge_tts
import ollama

# 眼睛控制
sys.path.insert(0, os.path.expanduser('~/reachy_mini_gaijin/src'))
try:
    from reachy_mini.io.eye_controller import EyeController
    EYE_AVAILABLE = True
except Exception as e:
    print(f'[WARN] 眼睛控制器加载失败: {e}')
    EYE_AVAILABLE = False


# ==========================================
#                 配置
# ==========================================
SAMPLE_RATE_IN = 44100        # USB 音频采样率
CHANNELS = 2                  # 立体声
TARGET_RATE = 16000           # ASR 目标采样率
BLOCK_DURATION_MS = 30        # 音频块时长
BLOCK_SIZE_IN = int(SAMPLE_RATE_IN * BLOCK_DURATION_MS / 1000)

# VAD
VAD_AGGRESSIVENESS = 2        # 0-3, 3 最激进
PRE_BUFFER_DURATION = 1.0     # 保留人声前多少秒
SILENCE_DURATION = 1.2        # 静音超过几秒算结束
MIN_SPEECH_DURATION = 0.3     # 最短人声（过滤噪音）
MAX_RECORD_DURATION = 15      # 最长录音（防卡死）

# 唤醒
WAKE_WORDS = ['墩墩', '噴噴', '喷喷', '蹲蹲', '登登', '等灯', '顿顿', '吨吨']
ACTIVE_DURATION = 120         # 唤醒后免唤醒时长（秒）

# LLM / TTS
OLLAMA_MODEL = 'qwen2:1.5b'
TTS_VOICE = 'zh-CN-XiaoxiaoNeural'
TTS_RATE = '+5%'

# ASR
ASR_PRIMARY = 'funasr'                          # 优先用 FunASR
FUNASR_MODEL_ID = 'iic/SenseVoiceSmall'         # 阿里达摩院 SenseVoice
ASR_FALLBACK = 'whisper'                        # fallback
WHISPER_MODEL_NAME = 'small'                    # fallback 用 small（比 tiny 准很多）
WHISPER_COMPUTE_TYPE = 'int8'
WHISPER_DOWNLOAD_ROOT = os.path.expanduser('~/reachy_mini_gaijin/models/faster-whisper')

# 硬件
AUDIO_INPUT_DEVICE = None       # None=自动找 USB
AUDIO_OUTPUT_DEVICE = 'plughw:2,0'  # aplay 播放设备

# 全局
_asr_model = None
_asr_engine_name = None
_mic_muted = threading.Event()  # TTS 播放时 set()
_last_active_time = 0
_eye = None


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


# ==========================================
#                眼睛状态
# ==========================================
def init_eye():
    global _eye
    if not EYE_AVAILABLE:
        log('眼睛不可用（EyeController 加载失败）')
        return
    try:
        _eye = EyeController()
        log('眼睛控制器初始化完成')
    except Exception as e:
        log(f'眼睛初始化失败: {e}')
        _eye = None


def set_eye_state(state):
    if _eye is not None:
        try:
            _eye.set_state(state)
        except Exception as e:
            log(f'眼睛状态失败: {e}')


# ==========================================
#                  ASR
# ==========================================
def _init_asr():
    """初始化 ASR 引擎（优先 FunASR，失败降级 faster-whisper small）"""
    global _asr_model, _asr_engine_name
    if ASR_PRIMARY == 'funasr':
        try:
            from funasr import AutoModel
            log(f'正在加载 FunASR + {FUNASR_MODEL_ID}...')
            log('（首次运行会从 ModelScope 下载 ~230MB 模型，请耐心等待 5-10 分钟）')
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
            log(f'→ 降级到 faster-whisper {WHISPER_MODEL_NAME}')

    # Fallback: faster-whisper small（比 tiny 中文好很多）
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
        log('  请检查模型是否已下载到 ~/reachy_mini_gaijin/models/faster-whisper/')
        raise


def _transcribe(audio_16k):
    """识别 16kHz float32 音频，返回文本"""
    if _asr_engine_name == 'funasr':
        return _transcribe_funasr(audio_16k)
    else:
        return _transcribe_whisper(audio_16k)


def _transcribe_funasr(audio_16k):
    """FunASR 识别"""
    import soundfile as sf
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
        tmp_path = f.name
    try:
        sf.write(tmp_path, audio_16k, TARGET_RATE)
        result = _asr_model.generate(
            input=tmp_path,
            cache={},
            language='auto',     # auto detect
            use_itn=True,        # 逆文本规范化（加标点）
        )
        if result and len(result) > 0:
            text = result[0].get('text', '')
            # 清理 SenseVoice 特殊 tag: <|zh|><|NEUTRAL|><|Speech|><|withitn|>
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
    """faster-whisper 识别（fallback）"""
    try:
        segments, _ = _asr_model.transcribe(
            audio_16k,
            language='zh',
            vad_filter=False,   # 我们自己 VAD
            beam_size=5,
        )
        return ' '.join(seg.text for seg in segments).strip()
    except Exception as e:
        log(f'faster-whisper 识别失败: {e}')
        return ''


# ==========================================
#              音频设备 + 降采样
# ==========================================
def list_input_devices():
    devices = sd.query_devices()
    log('输入设备列表:')
    for i, dev in enumerate(devices):
        if dev['max_input_channels'] > 0:
            log(f'  [{i}] {dev["name"]} (in={dev["max_input_channels"]})')


def find_input_device():
    if AUDIO_INPUT_DEVICE is not None:
        return AUDIO_INPUT_DEVICE
    list_input_devices()
    devices = sd.query_devices()
    for i, dev in enumerate(devices):
        if 'USB' in dev['name'] and dev['max_input_channels'] >= 2:
            log(f'→ 自动选择 USB 设备 [{i}] {dev["name"]}')
            return i
    log('→ 未找到 USB 设备，使用默认设备')
    return sd.default.device[0]


def downsample_block(stereo_int16):
    """立体声 int16 (44.1kHz) → 单声道 float32 (16kHz)"""
    if stereo_int16.ndim == 1:
        mono = stereo_int16.astype(np.float32) / 32768.0
    elif stereo_int16.shape[1] == 2:
        l = stereo_int16[:, 0].astype(np.float32) / 32768.0
        r = stereo_int16[:, 1].astype(np.float32) / 32768.0
        rms_l = np.sqrt(np.mean(l ** 2))
        rms_r = np.sqrt(np.mean(r ** 2))
        mono = l if rms_l > rms_r else r
    else:
        mono = stereo_int16[:, 0].astype(np.float32) / 32768.0

    # 线性插值降采样 44100 → 16000
    src_len = len(mono)
    dst_len = int(src_len * TARGET_RATE / SAMPLE_RATE_IN)
    if dst_len <= 1:
        return np.zeros(0, dtype=np.float32)
    src_idx = np.linspace(0, src_len - 1, dst_len)
    return np.interp(src_idx, np.arange(src_len), mono).astype(np.float32)


# ==========================================
#          VAD 智能录音器
# ==========================================
class VADRecorder:
    def __init__(self):
        try:
            self.vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)
            self.vad_available = True
            log('webrtcvad VAD 已启用')
        except Exception as e:
            log(f'webrtcvad 不可用 ({e})，降级到能量阈值 VAD')
            self.vad_available = False
            self.energy_threshold = 0.02

        self.pre_buffer = []
        self.speech_buffer = []
        self.is_speaking = False
        self.silence_start = None
        self.max_pre_blocks = int(PRE_BUFFER_DURATION * 1000 / BLOCK_DURATION_MS)
        self.max_record_blocks = int(MAX_RECORD_DURATION * 1000 / BLOCK_DURATION_MS)

    def _is_speech(self, block_16k):
        """判断一帧是否是人声"""
        if self.vad_available:
            pcm = (block_16k * 32767).astype(np.int16).tobytes()
            try:
                return self.vad.is_speech(pcm, TARGET_RATE)
            except Exception:
                pass
        # fallback: 能量阈值
        return np.sqrt(np.mean(block_16k ** 2)) > self.energy_threshold

    def feed(self, block_16k):
        """喂入一帧（30ms），返回完整录音（如果刚结束）或 None"""
        is_speech = self._is_speech(block_16k)

        if is_speech:
            if not self.is_speaking:
                # 人声开始 → 把前缓冲接到 speech_buffer
                self.is_speaking = True
                self.speech_buffer = list(self.pre_buffer)
                self.silence_start = None
                log('  [VAD] 人声开始')
            self.speech_buffer.append(block_16k)
        else:
            if self.is_speaking:
                # 录音中遇到静音，保留短静音（避免截断）
                self.speech_buffer.append(block_16k)
                if self.silence_start is None:
                    self.silence_start = time.time()
                elif time.time() - self.silence_start >= SILENCE_DURATION:
                    return self._finish()
            else:
                # 还没开始说话，更新前缓冲
                self.pre_buffer.append(block_16k)
                if len(self.pre_buffer) > self.max_pre_blocks:
                    self.pre_buffer.pop(0)

        # 超长录音保护
        if self.is_speaking and len(self.speech_buffer) > self.max_record_blocks:
            log(f'  [VAD] 录音超过 {MAX_RECORD_DURATION}s，强制结束')
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
#                LLM 调用
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


# ==========================================
#                 TTS 播放
# ==========================================
async def _tts_to_file(text, out_path):
    communicate = edge_tts.Communicate(text, voice=TTS_VOICE, rate=TTS_RATE)
    await communicate.save(out_path)


def tts_play(text):
    """TTS 播放（播放时静音麦克风防回声）"""
    if not text.strip():
        return
    mp3_path = None
    wav_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as f:
            mp3_path = f.name
        wav_path = mp3_path.replace('.mp3', '.wav')

        # edge-tts → mp3
        asyncio.run(_tts_to_file(text, mp3_path))

        # ffmpeg → wav (44.1kHz 立体声，与 USB 音频匹配)
        subprocess.run(
            ['ffmpeg', '-y', '-i', mp3_path, '-ar', '44100', '-ac', '2', wav_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # 静音麦克风 → 播放 → 解除静音
        _mic_muted.set()
        try:
            subprocess.run(
                ['aplay', '-D', AUDIO_OUTPUT_DEVICE, wav_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        finally:
            _mic_muted.clear()
    except Exception as e:
        log(f'TTS 错误: {e}')
    finally:
        for p in [mp3_path, wav_path]:
            if p:
                try:
                    os.unlink(p)
                except Exception:
                    pass


# ==========================================
#                主循环
# ==========================================
def main():
    global _last_active_time

    log('=' * 50)
    log('  墩墩 v9.2 - FunASR + SenseVoiceSmall')
    log('=' * 50)

    # 初始化 ASR
    _init_asr()
    log(f'当前 ASR 引擎: {_asr_engine_name}')

    # 初始化眼睛
    init_eye()
    set_eye_state('AWAIT')

    # 找输入设备
    input_dev = find_input_device()

    # 创建 VAD 录音器
    recorder = VADRecorder()

    # 唤醒计时
    _last_active_time = 0

    def audio_callback(indata, frames, time_info, status):
        """sounddevice 回调（30ms 一帧）"""
        global _last_active_time

        # TTS 播放时直接跳过（防回声）
        if _mic_muted.is_set():
            return

        # 降采样到 16kHz 单声道
        block_16k = downsample_block(indata)
        if len(block_16k) == 0:
            return

        # 喂给 VAD
        audio = recorder.feed(block_16k)
        if audio is None:
            return

        # ===== 识别 =====
        log(f'>>> 录音 {len(audio)/TARGET_RATE:.1f}s，开始识别...')
        set_eye_state('THINKING')
        text = _transcribe(audio)
        log(f'识别结果: {text!r}')

        if not text:
            set_eye_state('AWAIT')
            return

        # ===== 判断是否激活 =====
        active = (time.time() - _last_active_time) < ACTIVE_DURATION

        if not active:
            # 等唤醒
            if any(w in text for w in WAKE_WORDS):
                _last_active_time = time.time()
                log('>>> 唤醒成功！')
                set_eye_state('AWAKEN')
                tts_play('你好，我在呢！')
                set_eye_state('AWAIT')
            else:
                set_eye_state('AWAIT')
        else:
            # 激活模式：处理问题
            _last_active_time = time.time()
            set_eye_state('RESPONSE')
            response = call_llm(text)
            log(f'回复: {response[:80]}')
            tts_play(response)
            set_eye_state('AWAIT')

    # 启动流
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
