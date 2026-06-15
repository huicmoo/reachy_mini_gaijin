#include "Eye_Response.h"

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
// 左右加载点对象
// =====================================================

static lv_obj_t * left_dot1;
static lv_obj_t * left_dot2;
static lv_obj_t * left_dot3;

static lv_obj_t * right_dot1;
static lv_obj_t * right_dot2;
static lv_obj_t * right_dot3;

// =====================================================
// 动画计时
// =====================================================

static unsigned long lastAnim = 0;

static int currentDot = 0;

// =====================================================
// 创建三个点
// =====================================================

void createDots(
    lv_obj_t *parent,
    lv_obj_t **dot1,
    lv_obj_t **dot2,
    lv_obj_t **dot3
)
{
    // =====================================================
    // 第1个点
    // =====================================================

    *dot1 = lv_obj_create(parent);

    lv_obj_set_size(*dot1, 34, 34);

    lv_obj_set_style_radius(
        *dot1,
        LV_RADIUS_CIRCLE,
        0
    );

    lv_obj_set_style_bg_color(
        *dot1,
        lv_color_hex(0x3A3A3A),
        0
    );

    lv_obj_set_style_border_width(
        *dot1,
        0,
        0
    );

    lv_obj_set_style_outline_width(
        *dot1,
        0,
        0
    );

    lv_obj_set_style_shadow_width(
        *dot1,
        0,
        0
    );

    lv_obj_align(
        *dot1,
        LV_ALIGN_CENTER,
        -55,
        0
    );

    // =====================================================
    // 第2个点
    // =====================================================

    *dot2 = lv_obj_create(parent);

    lv_obj_set_size(*dot2, 34, 34);

    lv_obj_set_style_radius(
        *dot2,
        LV_RADIUS_CIRCLE,
        0
    );

    lv_obj_set_style_bg_color(
        *dot2,
        lv_color_hex(0x3A3A3A),
        0
    );

    lv_obj_set_style_border_width(
        *dot2,
        0,
        0
    );

    lv_obj_set_style_outline_width(
        *dot2,
        0,
        0
    );

    lv_obj_set_style_shadow_width(
        *dot2,
        0,
        0
    );

    lv_obj_align(
        *dot2,
        LV_ALIGN_CENTER,
        0,
        0
    );

    // =====================================================
    // 第3个点
    // =====================================================

    *dot3 = lv_obj_create(parent);

    lv_obj_set_size(*dot3, 34, 34);

    lv_obj_set_style_radius(
        *dot3,
        LV_RADIUS_CIRCLE,
        0
    );

    lv_obj_set_style_bg_color(
        *dot3,
        lv_color_hex(0x3A3A3A),
        0
    );

    lv_obj_set_style_border_width(
        *dot3,
        0,
        0
    );

    lv_obj_set_style_outline_width(
        *dot3,
        0,
        0
    );

    lv_obj_set_style_shadow_width(
        *dot3,
        0,
        0
    );

    lv_obj_align(
        *dot3,
        LV_ALIGN_CENTER,
        55,
        0
    );
}

// =====================================================
// 初始化
// =====================================================

void Eye_Response_Init()
{
    // =====================================================
    // 左屏
    // =====================================================

    lv_disp_set_default(disp);

    lv_obj_clean(lv_scr_act());

    lv_obj_set_style_bg_color(
        lv_scr_act(),
        lv_color_hex(0xD8D2C8),
        0
    );

    createDots(
        lv_scr_act(),
        &left_dot1,
        &left_dot2,
        &left_dot3
    );

    // =====================================================
    // 右屏
    // =====================================================

    lv_disp_set_default(disp2);

    lv_obj_clean(lv_scr_act());

    lv_obj_set_style_bg_color(
        lv_scr_act(),
        lv_color_hex(0xD8D2C8),
        0
    );

    createDots(
        lv_scr_act(),
        &right_dot1,
        &right_dot2,
        &right_dot3
    );

    vTaskDelay(pdMS_TO_TICKS(100));

}

// =====================================================
// 更新动画
// =====================================================

void Eye_Response_Update()
{
    vTaskDelay(pdMS_TO_TICKS(30));

    if (millis() - lastAnim > 300)
    {
        lastAnim = millis();

        currentDot++;

        if (currentDot > 2)
        {
            currentDot = 0;
        }

        // =====================================================
        // 左屏恢复灰色
        // =====================================================

        lv_obj_set_style_bg_color(
            left_dot1,
            lv_color_hex(0x3A3A3A),
            0
        );

        lv_obj_set_style_bg_color(
            left_dot2,
            lv_color_hex(0x3A3A3A),
            0
        );

        lv_obj_set_style_bg_color(
            left_dot3,
            lv_color_hex(0x3A3A3A),
            0
        );

        // =====================================================
        // 左屏点亮绿色
        // =====================================================

        if (currentDot == 0)
        {
            lv_obj_set_style_bg_color(
                left_dot1,
                lv_color_hex(0x00FF66),
                0
            );
        }

        if (currentDot == 1)
        {
            lv_obj_set_style_bg_color(
                left_dot2,
                lv_color_hex(0x00FF66),
                0
            );
        }

        if (currentDot == 2)
        {
            lv_obj_set_style_bg_color(
                left_dot3,
                lv_color_hex(0x00FF66),
                0
            );
        }

        // =====================================================
        // 右屏恢复灰色
        // =====================================================

        lv_obj_set_style_bg_color(
            right_dot1,
            lv_color_hex(0x3A3A3A),
            0
        );

        lv_obj_set_style_bg_color(
            right_dot2,
            lv_color_hex(0x3A3A3A),
            0
        );

        lv_obj_set_style_bg_color(
            right_dot3,
            lv_color_hex(0x3A3A3A),
            0
        );

        // =====================================================
        // 右屏点亮绿色
        // =====================================================

        if (currentDot == 0)
        {
            lv_obj_set_style_bg_color(
                right_dot1,
                lv_color_hex(0x00FF66),
                0
            );
        }

        if (currentDot == 1)
        {
            lv_obj_set_style_bg_color(
                right_dot2,
                lv_color_hex(0x00FF66),
                0
            );
        }

        if (currentDot == 2)
        {
            lv_obj_set_style_bg_color(
                right_dot3,
                lv_color_hex(0x00FF66),
                0
            );
        }
    }
}