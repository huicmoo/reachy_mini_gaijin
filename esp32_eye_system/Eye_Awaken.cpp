#include "Eye_Awaken.h"

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

// ===== 左右开心月牙眼对象 =====
static lv_obj_t * left_happy_eye;
static lv_obj_t * right_happy_eye;

void Eye_Awaken_Init()
{
    // =====================================================
    // 左屏
    // =====================================================

    lv_disp_set_default(disp);

    lv_obj_clean(lv_scr_act());

    // 背景米灰色

    lv_obj_set_style_bg_color(
        lv_scr_act(),
        lv_color_hex(0xD8D2C8),
        0
    );

    // =====================================================
    // 创建左开心月牙眼
    // =====================================================

    left_happy_eye = lv_arc_create(lv_scr_act());

    // 椭圆尺寸

    lv_obj_set_size(
        left_happy_eye,
        210,
        140
    );

    // 月牙弧线

    lv_arc_set_angles(
        left_happy_eye,
        180,
        360
    );

    // 关闭背景弧

    lv_arc_set_bg_angles(
        left_happy_eye,
        0,
        0
    );

    // 去旋钮

    lv_obj_remove_style(
        left_happy_eye,
        NULL,
        LV_PART_KNOB
    );

    // 去背景圆环

    lv_obj_remove_style(
        left_happy_eye,
        NULL,
        LV_PART_MAIN
    );

    // 粗线条

    lv_obj_set_style_arc_width(
        left_happy_eye,
        24,
        LV_PART_INDICATOR
    );

    // 黑色

    lv_obj_set_style_arc_color(
        left_happy_eye,
        lv_color_hex(0x000000),
        LV_PART_INDICATOR
    );

    // 圆角线头

    lv_obj_set_style_arc_rounded(
        left_happy_eye,
        true,
        LV_PART_INDICATOR
    );

    // 向下移动

    lv_obj_align(
        left_happy_eye,
        LV_ALIGN_CENTER,
        0,
        25
    );

    // =====================================================
    // 右屏
    // =====================================================

    lv_disp_set_default(disp2);

    lv_obj_clean(lv_scr_act());

    // 背景米灰色

    lv_obj_set_style_bg_color(
        lv_scr_act(),
        lv_color_hex(0xD8D2C8),
        0
    );

    // =====================================================
    // 创建右开心月牙眼
    // =====================================================

    right_happy_eye = lv_arc_create(lv_scr_act());

    lv_obj_set_size(
        right_happy_eye,
        210,
        140
    );

    lv_arc_set_angles(
        right_happy_eye,
        180,
        360
    );

    lv_arc_set_bg_angles(
        right_happy_eye,
        0,
        0
    );

    lv_obj_remove_style(
        right_happy_eye,
        NULL,
        LV_PART_KNOB
    );

    lv_obj_remove_style(
        right_happy_eye,
        NULL,
        LV_PART_MAIN
    );

    lv_obj_set_style_arc_width(
        right_happy_eye,
        24,
        LV_PART_INDICATOR
    );

    lv_obj_set_style_arc_color(
        right_happy_eye,
        lv_color_hex(0x000000),
        LV_PART_INDICATOR
    );

    lv_obj_set_style_arc_rounded(
        right_happy_eye,
        true,
        LV_PART_INDICATOR
    );

    lv_obj_align(
        right_happy_eye,
        LV_ALIGN_CENTER,
        0,
        25
    );

    vTaskDelay(pdMS_TO_TICKS(100));
}

void Eye_Awaken_Update()
{
    vTaskDelay(pdMS_TO_TICKS(10));
}