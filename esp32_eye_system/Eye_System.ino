// =============================================================================
// Eye_System.ino  — ESP32-S3-DualEye-LCD-1.28
//
// Changes vs original:
//   1. Added Serial (USB-CDC) command receiver → parseSerialCommand()
//   2. Removed the 5-second auto-switch test loop (replaced by real control)
//   3. ChangeEyeState() is now called from serial commands OR local logic
//   4. ERROR state has highest priority and overrides pending commands
//
// Serial Protocol (from Raspberry Pi 5):
//   Commands are ASCII strings terminated with '\n':
//     AWAIT\n       → EYE_AWAIT
//     NORMAL\n      → EYE_NORMAL
//     AWAKEN\n      → EYE_AWAKEN
//     LISTENING\n   → EYE_LISTENING
//     THINKING\n    → EYE_THINKING
//     RESPONSE\n    → EYE_RESPONSE
//     ERROR\n       → EYE_ERROR  (highest priority, locks out lower states)
//     STATUS\n      → replies with current state name + "\n" (optional query)
//
// Wiring (USB C):
//   Pi USB-A  ──  ESP32-S3 USB-C   (native USB CDC, no extra wiring)
//   Baud rate: 115200 (matches Pi side eye_controller.py)
// =============================================================================

#include "LCD_Driver.h"
#include "Audio_ES8311.h"
#include "LVGL_Driver.h"
#include "MIC_MSM.h"
#include "SD_Card.h"
#include "LVGL_Example.h"
#include "I2S_Driver.h"
#include "Button_Driver.h"
#include "I2C_Driver.h"
#include "BAT_Driver.h"

// =====================================================
// 表情模块
// =====================================================

#include "Eye_Await.h"
#include "Eye_Normal.h"
#include "Eye_Awaken.h"
#include "Eye_Listening.h"
#include "Eye_Thinking.h"
#include "Eye_Response.h"
#include "Eye_Error.h"

// =====================================================
// 表情状态枚举
// =====================================================

enum EyeState
{
    EYE_NONE,     // sentinel: before any eye state is set
    EYE_AWAIT,
    EYE_NORMAL,
    EYE_AWAKEN,
    EYE_LISTENING,
    EYE_THINKING,
    EYE_RESPONSE,
    EYE_ERROR
};

// =====================================================
// 状态优先级（值越大优先级越高）
// =====================================================

static const uint8_t STATE_PRIORITY[] = {
    0,  // EYE_NONE (unused sentinel)
    0,  // EYE_AWAIT
    0,  // EYE_NORMAL
    4,  // EYE_AWAKEN
    1,  // EYE_LISTENING
    2,  // EYE_THINKING
    3,  // EYE_RESPONSE
    5   // EYE_ERROR  — highest, cannot be overridden by lower priority
};

// =====================================================
// 当前状态 & 串口解析缓冲
// =====================================================

EyeState currentState        = EYE_NONE;
EyeState requestedState      = EYE_NONE;
bool     newStateRequested    = false;

// ERROR 优先级锁定计时（毫秒）
static const uint32_t ERROR_LOCKOUT_MS = 3000;
static uint32_t       errorLockoutUntil = 0;

// 串口接收缓冲
static char   rxBuf[32];
static uint8_t rxIdx = 0;

// =====================================================
// 切换状态函数
// =====================================================

void ChangeEyeState(EyeState newState)
{
    // Priority gate: if ERROR lock-out is active, reject lower-priority states
    if (millis() < errorLockoutUntil && newState != EYE_ERROR)
    {
        return;  // silently ignore during lock-out
    }

    // Only switch if actually different (avoids unnecessary LVGL cleans)
    if (newState == currentState) return;

    currentState = newState;

    // Activate ERROR lock-out
    if (newState == EYE_ERROR)
    {
        errorLockoutUntil = millis() + ERROR_LOCKOUT_MS;
    }

    // -------------------------------------------------
    // 清空左右屏
    // -------------------------------------------------

    lv_disp_set_default(disp);
    lv_obj_clean(lv_scr_act());

    lv_disp_set_default(disp2);
    lv_obj_clean(lv_scr_act());

    // -------------------------------------------------
    // 初始化对应表情
    // -------------------------------------------------

    switch(currentState)
    {
        case EYE_NONE:
            // should not reach here, but safe fallback
            break;

        case EYE_AWAIT:
            Eye_Await_Init();
            break;

        case EYE_NORMAL:
            Eye_Normal_Init();
            break;

        case EYE_AWAKEN:
            Eye_Awaken_Init();
            break;

        case EYE_LISTENING:
            Eye_Listening_Init();
            break;

        case EYE_THINKING:
            Eye_Thinking_Init();
            break;

        case EYE_RESPONSE:
            Eye_Response_Init();
            break;

        case EYE_ERROR:
            Eye_Error_Init();
            break;
    }
}

// =====================================================
// 串口命令解析（非阻塞，每帧调用）
// =====================================================

void parseSerialCommand()
{
    // Drain all available bytes from Serial (USB CDC)
    while (Serial.available() > 0)
    {
        char c = (char)Serial.read();

        // Newline marks end of command
        if (c == '\n' || c == '\r')
        {
            if (rxIdx > 0)
            {
                rxBuf[rxIdx] = '\0';
                rxIdx = 0;

                // --- Match command strings ---
                EyeState target = currentState;  // default: no change

                if      (strcmp(rxBuf, "AWAIT")     == 0) target = EYE_AWAIT;
                else if (strcmp(rxBuf, "NORMAL")     == 0) target = EYE_NORMAL;
                else if (strcmp(rxBuf, "AWAKEN")     == 0) target = EYE_AWAKEN;
                else if (strcmp(rxBuf, "LISTENING")  == 0) target = EYE_LISTENING;
                else if (strcmp(rxBuf, "THINKING")   == 0) target = EYE_THINKING;
                else if (strcmp(rxBuf, "RESPONSE")   == 0) target = EYE_RESPONSE;
                else if (strcmp(rxBuf, "ERROR")      == 0) target = EYE_ERROR;
                else if (strcmp(rxBuf, "STATUS")     == 0)
                {
                    // Optional: reply with current state name for diagnostics
                    static const char* stateNames[] = {
                        "NONE", "AWAIT", "NORMAL", "AWAKEN", "LISTENING",
                        "THINKING", "RESPONSE", "ERROR"
                    };
                    Serial.println(stateNames[currentState]);
                    return;
                }
                else
                {
                    // unknown command — report it for debugging
                    Serial.print("UNKNOWN_CMD: ");
                    Serial.println(rxBuf);
                }

                if (target != currentState)
                {
                    // Debug: echo the state change
                    static const char* stateNames[] = {
                        "NONE", "AWAIT", "NORMAL", "AWAKEN", "LISTENING",
                        "THINKING", "RESPONSE", "ERROR"
                    };
                    Serial.print("OK ");
                    Serial.println(stateNames[target]);
                    ChangeEyeState(target);
                }
                else
                {
                    // Debug: same state, skipped
                    Serial.println("SAME_STATE");
                }
            }
        }
        else
        {
            // Accumulate bytes (guard against overflow)
            if (rxIdx < (sizeof(rxBuf) - 1))
            {
                rxBuf[rxIdx++] = c;
            }
            else
            {
                // Buffer overflow — reset and discard
                rxIdx = 0;
            }
        }
    }
}

// =====================================================
// setup
// =====================================================

void setup()
{
    // USB CDC Serial — must match baud in eye_controller.py
    Serial.begin(115200);

    Flash_test();

    I2C_Init();

    Button_Init();

    BAT_Init();

    SD_Init();

    MIC_Init();

    Audio_Init();

    LCD_INIT();

    // -------------------------------------------------
    // 双屏背光校准
    // -------------------------------------------------

    Set_Backlight1(90);
    Set_Backlight2(65);

    Lvgl_Init();

    // -------------------------------------------------
    // 先启动 LVGL 渲染任务，再初始化表情
    // 确保 ChangeEyeState 时渲染线程已就绪
    // -------------------------------------------------

    LVGL_Start();

    Simulated_Touch_Init();

    // 等待 LVGL 任务真正运行起来（100ms 足够）
    vTaskDelay(pdMS_TO_TICKS(100));

    // 初始化默认表情（此时渲染线程已在跑，clean + Init 会被正确渲染）
    ChangeEyeState(EYE_AWAIT);

    // -------------------------------------------------
    // 调试：确认串口就绪
    // -------------------------------------------------
    Serial.println("EYE_SYSTEM_READY");
    Serial.println("Commands: AWAIT NORMAL AWAKEN LISTENING THINKING RESPONSE ERROR STATUS");
}

// =====================================================
// loop
// =====================================================

void loop()
{
    vTaskDelay(pdMS_TO_TICKS(10));

    // -------------------------------------------------
    // 1. 读取并执行来自 Raspberry Pi 的串口指令（非阻塞）
    // -------------------------------------------------

    parseSerialCommand();

    // -------------------------------------------------
    // 2. 更新当前表情动画
    // -------------------------------------------------

    switch(currentState)
    {
        case EYE_NONE:
            // waiting for first state — no update needed
            break;

        case EYE_AWAIT:
            Eye_Await_Update();
            break;

        case EYE_NORMAL:
            Eye_Normal_Update();
            break;

        case EYE_AWAKEN:
            Eye_Awaken_Update();
            break;

        case EYE_LISTENING:
            Eye_Listening_Update();
            break;

        case EYE_THINKING:
            Eye_Thinking_Update();
            break;

        case EYE_RESPONSE:
            Eye_Response_Update();
            break;

        case EYE_ERROR:
            Eye_Error_Update();
            break;
    }

    // -------------------------------------------------
    // (测试用自动循环已移除 — 由 Raspberry Pi 控制)
    // 如需恢复测试模式，可在此处添加计时器逻辑
    // -------------------------------------------------
}
