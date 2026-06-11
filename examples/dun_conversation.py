#!/usr/bin/env python3
"""
墩墩（DunDun）完整语音对话系统
=====================================
本地运行，无需 API Key：
  - 唤醒词检测：VAD + faster-whisper
  - ASR：faster-whisper（本地）
  - LLM：Ollama 本地大模型
  - TTS：edge-tts（微软 Edge 在线 TTS，免费）
  - 眼睛状态：ESP32-S3 双屏动态表情

交互流程：
  [待机 AWAIT] → 喊"墩墩" → [AWAKEN + "你好，我在"]
                                    ↓
                              [LISTENING 2s]
                                    ↓
                              用户提问 → [THINKING]
                                    ↓
                              LLM 生成答案 → [RESPONSE]
                                    ↓
                              TTS 播报 → [回到 AWAIT]

用法：
    cd ~/reachy_mini_gaijin-main
    python3 examples/dun_conversation.py

前置依赖（见下方 install_on_pi5.sh）
"""

import os
import sys
import time
import wave
import tempfile
import threading
import asyncio
from pathlib import Path

import numpy as np
import requests

# ---- 音频录制 ----
import sounddevice as sd
import webrtcvad

# ---- ASR ----
from faster_whisper import WhisperModel

# ---- TTS ----
import edge_tts

# ---- 眼睛状态机 ----
# 将项目源码目录加入 Python 路径
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from reachy_mini.io import EyeController
from reachy_mini.utils.robot_state_machine import RobotStateMachine


# =============================================================================
# 配置区（根据你的环境修改）
# =============================================================================
SERIAL_PORT = "/dev/ttyACM0"          # ESP32 串口
AUDIO_DEVICE = "plughw:2,0"           # seeed-2mic-voicecard (录音+播放)
GREETING_WAV = str(PROJECT_ROOT / "greetings" / "nihao_wozai.wav")

WHISPER_MODEL = "base"                # tiny/base/small，Pi5 建议 base
WHISPER_COMPUTE = "int8"              # int8 量化省内存
SAMPLE_RATE = 16000                   # 16kHz，webrtcvad 要求
VAD_AGGRESSIVENESS = 2                # 0-3，越大越严格

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "qwen2"                # 先 ollama pull qwen2

TTS_VOICE = "zh-CN-XiaoxiaoNeural"    # 晓晓女声，也可换 zh-CN-YunxiNeural（男声）

# 录音参数
CHUNK_MS = 30                         # VAD 帧长，必须是 10/20/30
CHUNK_SAMPLES = int(SAMPLE_RATE * CHUNK_MS / 1000)
MAX_RECORD_SEC = 12                   # 单次录音最大时长
SILENCE_TIMEOUT_SEC = 1.5             # 检测到静音多久后停止录音
WAKE_RECORD_SEC = 2.5                 # 唤醒词检测录音时长


class DunDunBot:
    """
    墩墩机器人：完整语音交互循环
    """

    def __init__(self):
        self._running = False
        self._lock = threading.Lock()

        # ---- 眼睛控制器 ----
        print("[*] 初始化眼睛控制器...")
        self.eyes = EyeController(port=SERIAL_PORT)

        # ---- 状态机 ----
        self.sm = RobotStateMachine(
            eye_controller=self.eyes,
            on_play_audio=self._play_wav,
        )

        # ---- VAD ----
        print("[*] 初始化 VAD...")
        self.vad = webrtcvad.Vad(VAD_AGGRESSIVENESS)

        # ---- Whisper ASR ----
        print(f"[*] 加载 Whisper 模型 ({WHISPER_MODEL})...")
        self.whisper = WhisperModel(
            WHISPER_MODEL,
            device="cpu",
            compute_type=WHISPER_COMPUTE,
        )
        print("[+] Whisper 加载完成")

        # ---- TTS 事件循环（复用）----
        self._tts_loop = asyncio.new_event_loop()
        self._tts_thread = threading.Thread(target=self._run_tts_loop, daemon=True)
        self._tts_thread.start()

    # ------------------------------------------------------------------
    # 音频播放（ALSA 直接播放）
    # ------------------------------------------------------------------
    def _play_wav(self, path: str):
        """播放 WAV 文件，使用 aplay 直接操作 ALSA。"""
        os.system(f"aplay -q -D {AUDIO_DEVICE} '{path}' 2>/dev/null")

    def _play_mp3(self, path: str):
        """播放 MP3 文件（TTS 输出）。需要 ffmpeg 转码或直接用 ffplay。"""
        # 先转成 wav 再播放，避免 mp3 解码器问题
        tmp_wav = f"/tmp/dun_play_{int(time.time()*1000)}.wav"
        os.system(f"ffmpeg -y -i '{path}' -ar 44100 -ac 2 '{tmp_wav}' 2>/dev/null")
        self._play_wav(tmp_wav)
        try:
            os.remove(tmp_wav)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # TTS 异步事件循环（线程内运行）
    # ------------------------------------------------------------------
    def _run_tts_loop(self):
        asyncio.set_event_loop(self._tts_loop)
        self._tts_loop.run_forever()

    def _tts_generate_sync(self, text: str, output_path: str):
        """在线程安全的同步接口中生成 TTS。"""
        future = asyncio.run_coroutine_threadsafe(
            self._tts_generate_async(text, output_path),
            self._tts_loop,
        )
        future.result(timeout=30)

    async def _tts_generate_async(self, text: str, output_path: str):
        """edge-tts 异步生成。"""
        communicate = edge_tts.Communicate(text, TTS_VOICE)
        await communicate.save(output_path)

    # ------------------------------------------------------------------
    # 录音
    # ------------------------------------------------------------------
    def _record_audio(self, max_sec: float = MAX_RECORD_SEC,
                      silence_sec: float = SILENCE_TIMEOUT_SEC) -> str:
        """
        录音并保存为 WAV，返回临时文件路径。
        当检测到 silence_sec 秒的静音时自动停止（但最少录 0.5s）。
        """
        print("    [🎙️  录音中...]", end="", flush=True)

        frames = []
        silent_chunks = 0
        max_chunks = int(max_sec * 1000 / CHUNK_MS)
        silence_threshold = int(silence_sec * 1000 / CHUNK_MS)
        min_chunks = int(0.5 * 1000 / CHUNK_MS)

        stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK_SAMPLES,
            dtype="int16",
            channels=1,
            device=AUDIO_DEVICE,
        )
        stream.start()

        try:
            for _ in range(max_chunks):
                data, _ = stream.read(CHUNK_SAMPLES)
                frame = data.tobytes()
                frames.append(frame)

                is_speech = self.vad.is_speech(frame, SAMPLE_RATE)

                if not is_speech:
                    silent_chunks += 1
                    if silent_chunks > silence_threshold and len(frames) > min_chunks:
                        break
                else:
                    silent_chunks = 0
                    print(".", end="", flush=True)

        finally:
            stream.stop()
            stream.close()

        print(" 完成")

        # 保存临时 WAV
        wav_path = f"/tmp/dun_record_{int(time.time()*1000)}.wav"
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b"".join(frames))

        return wav_path

    def _record_fixed_duration(self, duration_sec: float) -> str:
        """录固定时长的音频，用于唤醒词短检测。"""
        n_samples = int(SAMPLE_RATE * duration_sec)
        n_chunks = n_samples // CHUNK_SAMPLES

        frames = []
        stream = sd.RawInputStream(
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK_SAMPLES,
            dtype="int16",
            channels=1,
            device=AUDIO_DEVICE,
        )
        stream.start()
        try:
            for _ in range(n_chunks):
                data, _ = stream.read(CHUNK_SAMPLES)
                frames.append(data.tobytes())
        finally:
            stream.stop()
            stream.close()

        wav_path = f"/tmp/dun_wake_{int(time.time()*1000)}.wav"
        with wave.open(wav_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b"".join(frames))

        return wav_path

    # ------------------------------------------------------------------
    # ASR
    # ------------------------------------------------------------------
    def transcribe(self, wav_path: str) -> str:
        """Whisper 语音识别，返回中文文本。"""
        segments, _ = self.whisper.transcribe(
            wav_path,
            language="zh",
            beam_size=5,
            condition_on_previous_text=False,
        )
        text = "".join(seg.text for seg in segments).strip()
        return text

    # ------------------------------------------------------------------
    # LLM（Ollama）
    # ------------------------------------------------------------------
    def ask_llm(self, question: str) -> str:
        """
        调用本地 Ollama 生成回答。
        提示词中注入"墩墩"人格：轻松但要专业。
        """
        system_prompt = (
            "你是一个叫'墩墩'的机器人助手。"
            "说话风格轻松但要专业，回答简洁（30-50字），用中文。"
            "如果用户的问题不清楚，礼貌地请用户再说一遍。"
        )
        full_prompt = f"{system_prompt}\n\n用户：{question}\n墩墩："

        try:
            resp = requests.post(
                OLLAMA_URL,
                json={
                    "model": OLLAMA_MODEL,
                    "prompt": full_prompt,
                    "stream": False,
                    "options": {"temperature": 0.7, "num_predict": 80},
                },
                timeout=60,
            )
            resp.raise_for_status()
            answer = resp.json().get("response", "").strip()
            # 清理可能的重复前缀
            for prefix in ["墩墩：", "墩墩:", "机器人：", "机器人:"]:
                if answer.startswith(prefix):
                    answer = answer[len(prefix):].strip()
            return answer or "嗯，我没太明白，能再说一遍吗？"
        except requests.ConnectionError:
            return "抱歉，我的大脑（Ollama）好像没启动，请检查服务。"
        except Exception as e:
            print(f"    [LLM 错误: {e}]")
            return "抱歉，我刚才走神了，请再说一次。"

    # ------------------------------------------------------------------
    # TTS
    # ------------------------------------------------------------------
    def speak(self, text: str):
        """文字转语音并播放。"""
        print(f"    [🔊 TTS: {text[:40]}...]")
        mp3_path = f"/tmp/dun_tts_{int(time.time()*1000)}.mp3"
        self._tts_generate_sync(text, mp3_path)
        self._play_mp3(mp3_path)
        try:
            os.remove(mp3_path)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 唤醒词检测
    # ------------------------------------------------------------------
    def detect_wake_word(self) -> bool:
        """
        录一段短音频，识别是否包含"墩墩"。
        返回 True 表示检测到唤醒词。
        """
        wav_path = self._record_fixed_duration(WAKE_RECORD_SEC)
        try:
            text = self.transcribe(wav_path)
            print(f"    [听到: {text}]")
            return "墩墩" in text
        finally:
            try:
                os.remove(wav_path)
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run(self):
        """
        墩墩主交互循环。
        """
        self.eyes.start()
        self.sm.start()
        self._running = True

        print("\n" + "=" * 50)
        print("         🎙️  墩墩语音对话系统已启动")
        print("         喊'墩墩'来唤醒我")
        print("=" * 50 + "\n")

        try:
            while self._running:
                # -------- 待机：持续监听唤醒词 --------
                print("[💤 待机] 等待唤醒...")
                self.sm._set_eye_state(self.sm._eye_states.AWAIT)

                woke = False
                while not woke and self._running:
                    woke = self.detect_wake_word()

                if not self._running:
                    break

                # -------- 唤醒 --------
                print(">>> [🌟 唤醒] 你好，我在！")
                self.sm.on_wake_word_detected(greeting_audio=GREETING_WAV)

                # 等问候语播完 + 自动切换到 LISTENING（状态机内部处理）
                time.sleep(2.5)

                # -------- 聆听用户问题 --------
                print(">>> [👂 请提问...]")
                self.sm.on_listening_start()
                question_wav = self._record_audio()
                self.sm.on_listening_end()

                # -------- ASR --------
                print(">>> [📝 识别中...]")
                question = self.transcribe(question_wav)
                try:
                    os.remove(question_wav)
                except OSError:
                    pass

                if not question:
                    print("    [没听清，跳过]")
                    self.sm.on_tts_end()  # 回到待机
                    continue

                print(f"    [用户]: {question}")

                # -------- LLM --------
                print(">>> [🧠 思考中...]")
                self.sm.on_llm_start()
                answer = self.ask_llm(question)
                self.sm.on_llm_end()
                print(f"    [墩墩]: {answer}")

                # -------- TTS 播报 --------
                self.sm.on_tts_start()
                self.speak(answer)
                self.sm.on_tts_end()

                print(">>> [✅ 一轮对话结束]\n")
                time.sleep(0.5)

        except KeyboardInterrupt:
            print("\n[!] 收到中断信号，正在退出...")
        finally:
            self._running = False
            self.sm.stop()
            self.eyes.stop()
            self._tts_loop.call_soon_threadsafe(self._tts_loop.stop)
            print("[+] 已安全退出")


# =============================================================================
# 入口
# =============================================================================
if __name__ == "__main__":
    bot = DunDunBot()
    bot.run()
