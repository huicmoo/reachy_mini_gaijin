# 眼睛联配集成说明

本文档说明如何在 `reachy_mini_gaijin` 项目中使用 ESP32-S3 双屏眼睛模块。

---

## 新增文件

| 文件 | 作用 |
|------|------|
| `src/reachy_mini/io/eye_controller.py` | Pi→ESP32 串口通信核心，非阻塞、带优先级 |
| `src/reachy_mini/utils/robot_state_machine.py` | 机器人中央状态机，协调五维状态 |
| `examples/app_with_eyes.py` | 完整集成示例 |

## 修改文件

| 文件 | 改动 |
|------|------|
| `src/reachy_mini/io/__init__.py` | 暴露 EyeController, EyeState |
| `src/reachy_mini/__init__.py` | 暴露 EyeController, EyeState, RobotStateMachine |

---

## 状态映射

| 机器人状态 | 眼睛表情 | 说明 |
|-----------|---------|------|
| 待机 | Eye_Await | 等待唤醒词 |
| 唤醒确认 | Eye_Awaken | **听到"墩墩"** → 回答"你好，我在"（这不是正式回答） |
| 聆听用户 | Eye_Listening | 等待用户具体提问 |
| LLM推理 / 思考 | Eye_Thinking | 处理用户问题中 |
| TTS播报 | Eye_Response | **正式回答**用户问题时播报 |
| 普通状态 | Eye_Normal | 日常状态 |
| 异常（摄像头/麦克风/散热等） | Eye_Error（优先级最高，锁定3秒） | |

### 关键区分：唤醒确认 vs 正式回答

- **唤醒确认**（`on_wake_word_detected()`）：听到名字"墩墩"时触发。眼睛显示 AWAKEN，播放问候语"你好，我在"。**这不属于 RESPONSE 状态**。
- **正式回答**（`on_tts_start()`）：用户提出具体问题后，LLM 生成答案并通过 TTS 播报时触发。眼睛显示 RESPONSE。

---

## 串口协议

Pi 发送一行 ASCII 字符串 + `\n`，ESP32 立即响应：

```
AWAIT\n   NORMAL\n   AWAKEN\n   LISTENING\n   THINKING\n   RESPONSE\n   ERROR\n
```

---

## 快速使用

```python
from reachy_mini import EyeController, EyeState, RobotStateMachine

# 1. 启动眼睛控制器（自动检测端口）
eyes = EyeController()
eyes.start()

# 2. 启动中央状态机
sm = RobotStateMachine(eye_controller=eyes)
sm.start()

# 3. 在机器人各模块回调中调用：
sm.on_wake_word_detected()    # 唤醒 → AWAKEN 2秒 → LISTENING
sm.on_llm_start()             # → THINKING
sm.on_tts_start()             # → RESPONSE
sm.on_tts_end()               # → AWAIT

sm.notify_error("camera")     # → ERROR（3秒锁定）
sm.clear_error()              # 恢复 → AWAIT

# 也可以直接控制眼睛
eyes.set_state(EyeState.AWAKEN)

# 4. 结束时清理
sm.stop()
eyes.stop()
```

---

## "墩墩" 唤醒流程

```python
# 配置状态机（带语音播放回调）
def play_greeting(audio_path: str):
    reachy.media.play_sound(audio_path)

sm = RobotStateMachine(
    eye_controller=eyes,
    on_play_audio=play_greeting,
)

# 听到唤醒词 "墩墩"
sm.on_wake_word_detected(greeting_audio="greetings/nihao_wozai.wav")
#   → Eye: AWAKEN
#   → Audio: "你好，我在"
#   → 2秒后自动 → Eye: LISTENING

# 用户开始提问
sm.on_listening_start()
#   → Eye: LISTENING

# 用户说完，开始处理
sm.on_listening_end()
#   → Eye: THINKING

# LLM 生成答案
sm.on_llm_start()
sm.on_llm_end()

# TTS 播报正式回答
sm.on_tts_start()
#   → Eye: RESPONSE  ← 这才是正式回答状态
sm.on_tts_end()
#   → Eye: AWAIT
```

---

## Pi 5 启动命令

```bash
# 查看 ESP32 端口
ls /dev/ttyACM* /dev/ttyUSB*

# 安装 pyserial
pip3 install pyserial

# 运行示例
python3 examples/app_with_eyes.py

# 指定端口
python3 examples/app_with_eyes.py --eye-port /dev/ttyACM0

# 无眼睛硬件时
python3 examples/app_with_eyes.py --no-eyes
```
