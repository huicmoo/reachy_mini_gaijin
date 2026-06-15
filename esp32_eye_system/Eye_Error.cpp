#include "Eye_Error.h"

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
// 左右屏 X 眼对象
// =====================================================

static lv_obj_t * left_line1;
static lv_obj_t * left_line2;

static lv_obj_t * right_line1;
static lv_obj_t * right_line2;

// =====================================================
// 闪烁状态
// =====================================================

bool visible_state = true;

unsigned long lastBlink = 0;

// =====================================================
// X 线条坐标
// =====================================================

static lv_point_t line1_points[] = {
    {40, 40},
    {200, 200}
};

static lv_point_t line2_points[] = {
    {200, 40},
    {40, 200}
};

// =====================================================
// 创建一个 X 眼
// =====================================================

void createXEye(
    lv_obj_t **line1,
    lv_obj_t **line2
)
{
    // 第一条线 \

    *line1 = lv_line_create(lv_scr_act());

    lv_line_set_points(
        *line1,
        line1_points,
        2
    );

    lv_obj_set_style_line_width(
        *line1,
        24,
        0
    );

    lv_obj_set_style_line_color(
        *line1,
        lv_color_hex(0xFF0000),
        0
    );

    lv_obj_set_style_line_rounded(
        *line1,
        true,
        0
    );

    // 第二条线 /

    *line2 = lv_line_create(lv_scr_act());

    lv_line_set_points(
        *line2,
        line2_points,
        2
    );

    lv_obj_set_style_line_width(
        *line2,
        24,
        0
    );

    lv_obj_set_style_line_color(
        *line2,
        lv_color_hex(0xFF0000),
        0
    );

    lv_obj_set_style_line_rounded(
        *line2,
        true,
        0
    );
}

// =====================================================
// 初始化
// =====================================================

void Eye_Error_Init()
{
    // =====================================================
    // 左屏
    // =====================================================

    lv_disp_set_default(disp);

    lv_obj_clean(lv_scr_act());

    // 黑色背景

    lv_obj_set_style_bg_color(
        lv_scr_act(),
        lv_color_hex(0x000000),
        0
    );

    createXEye(
        &left_line1,
        &left_line2
    );

    // =====================================================
    // 右屏
    // =====================================================

    lv_disp_set_default(disp2);

    lv_obj_clean(lv_scr_act());

    // 黑色背景

    lv_obj_set_style_bg_color(
        lv_scr_act(),
        lv_color_hex(0x000000),
        0
    );

    createXEye(
        &right_line1,
        &right_line2
    );

    // 重置状态

    visible_state = true;

    lastBlink = millis();
}

// =====================================================
// 更新动画
// =====================================================

void Eye_Error_Update()
{
    // =====================================================
    // 每1秒闪烁
    // =====================================================

    if (millis() - lastBlink > 1000)
    {
        lastBlink = millis();

        visible_state = !visible_state;

        // =================================================
        // 显示
        // =================================================

        if (visible_state)
        {
            // 左屏

            lv_obj_clear_flag(
                left_line1,
                LV_OBJ_FLAG_HIDDEN
            );

            lv_obj_clear_flag(
                left_line2,
                LV_OBJ_FLAG_HIDDEN
            );

            // 右屏

            lv_obj_clear_flag(
                right_line1,
                LV_OBJ_FLAG_HIDDEN
            );

            lv_obj_clear_flag(
                right_line2,
                LV_OBJ_FLAG_HIDDEN
            );
        }

        // =================================================
        // 隐藏
        // =================================================

        else
        {
            // 左屏

            lv_obj_add_flag(
                left_line1,
                LV_OBJ_FLAG_HIDDEN
            );

            lv_obj_add_flag(
                left_line2,
                LV_OBJ_FLAG_HIDDEN
            );

            // 右屏

            lv_obj_add_flag(
                right_line1,
                LV_OBJ_FLAG_HIDDEN
            );

            lv_obj_add_flag(
                right_line2,
                LV_OBJ_FLAG_HIDDEN
            );
        }
    }
}