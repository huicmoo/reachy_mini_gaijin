#!/bin/bash
# =============================================================================
# 墩墩语音对话系统 - Pi 5 一键安装脚本
# =============================================================================
# 用法：
#   cd ~/reachy_mini_gaijin-main
#   chmod +x examples/install_on_pi5.sh
#   bash examples/install_on_pi5.sh
# =============================================================================

set -e

echo "=========================================="
echo "  墩墩语音系统 - Pi 5 依赖安装"
echo "=========================================="

# ---- 1. 系统依赖 ----
echo "[*] 安装系统音频依赖..."
sudo apt-get update
sudo apt-get install -y \
    portaudio19-dev \
    python3-dev \
    python3-pip \
    ffmpeg \
    espeak-ng \
    libsndfile1

# ---- 2. Python 依赖 ----
echo "[*] 安装 Python 包（使用 --break-system-packages）..."
# 注意：Pi 5 的 Debian 系统限制了 pip，需要 --break-system-packages
pip3 install --break-system-packages \
    sounddevice \
    soundfile \
    webrtcvad \
    numpy \
    requests \
    edge-tts \
    faster-whisper

# ---- 3. 安装 Ollama ----
if ! command -v ollama &> /dev/null; then
    echo "[*] 安装 Ollama..."
    curl -fsSL https://ollama.com/install.sh | sh
else
    echo "[+] Ollama 已安装"
fi

# ---- 4. 拉取 LLM 模型 ----
echo "[*] 拉取 qwen2 模型（约 4GB，首次下载需要几分钟）..."
ollama pull qwen2

# ---- 5. 验证安装 ----
echo ""
echo "=========================================="
echo "  安装完成！验证项："
echo "=========================================="
echo ""
echo "1. Ollama 服务状态:"
systemctl --user status ollama --no-pager || true

echo ""
echo "2. 已安装模型:"
ollama list || true

echo ""
echo "3. Python 包检查:"
python3 -c "import sounddevice, webrtcvad, faster_whisper, edge_tts; print('  [OK] 所有 Python 包已安装')" || echo "  [X] 部分包缺失"

echo ""
echo "=========================================="
echo "  下一步："
echo "=========================================="
echo "  1. 确保 ESP32 已连接到 Pi 5 的 USB 口"
echo "  2. 运行测试："
echo "       cd ~/reachy_mini_gaijin-main"
echo "       python3 examples/dun_conversation.py"
echo ""
