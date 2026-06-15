#include "Eye_Thinking.h"

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

#include <math.h>

// =====================================================
// 左右眼对象
// =====================================================

static lv_obj_t * left_eye;
static lv_obj_t * right_eye;

static lv_obj_t * left_pupil;
static lv_obj_t * right_pupil;

// =====================================================
// 圆周运动参数
// =====================================================

static float angle = 0.0;

// =====================================================
// 初始化
// =====================================================

void Eye_Thinking_Init()
{
    // =====================================================
    // 左屏
    // =====================================================

    lv_disp_set_default(disp);

    lv_obj_clean(lv_scr_act());

    // 背景颜色

    lv_obj_set_style_bg_color(
        lv_scr_act(),
        lv_color_hex(0xD8D2C8),
        0
    );

    // =====================================================
    // 创建左眼
    // =====================================================

    left_eye = lv_obj_create(lv_scr_act());

    lv_obj_set_size(left_eye, 238, 238);

    lv_obj_center(left_eye);

    lv_obj_set_pos(left_eye, 1, 1);

    lv_obj_set_style_radius(
        left_eye,
        LV_RADIUS_CIRCLE,
        0
    );

    lv_obj_set_style_bg_color(
        left_eye,
        lv_color_hex(0xD8D2C8),
        0
    );

    lv_obj_set_style_bg_opa(
        left_eye,
        LV_OPA_COVER,
        0
    );

    // 去边框

    lv_obj_set_style_border_width(
        left_eye,
        0,
        0
    );

    lv_obj_set_style_outline_width(
        left_eye,
        0,
        0
    );

    lv_obj_set_style_shadow_width(
        left_eye,
        0,
        0
    );

    lv_obj_set_style_pad_all(
        left_eye,
        0,
        0
    );

    lv_obj_clear_flag(
        left_eye,
        LV_OBJ_FLAG_SCROLLABLE
    );

    // =====================================================
    // 创建左瞳孔
    // =====================================================

    left_pupil = lv_obj_create(left_eye);

    lv_obj_set_size(left_pupil, 130, 130);

    lv_obj_set_style_radius(
        left_pupil,
        LV_RADIUS_CIRCLE,
        0
    );

    lv_obj_set_style_bg_color(
        left_pupil,
        lv_color_hex(0x000000),
        0
    );

    lv_obj_set_style_bg_opa(
        left_pupil,
        LV_OPA_COVER,
        0
    );

    lv_obj_set_style_border_width(
        left_pupil,
        0,
        0
    );

    lv_obj_align(
        left_pupil,
        LV_ALIGN_CENTER,
        0,
        0
    );

    // =====================================================
    // 右屏
    // =====================================================

    lv_disp_set_default(disp2);

    lv_obj_clean(lv_scr_act());

    // 背景颜色

    lv_obj_set_style_bg_color(
        lv_scr_act(),
        lv_color_hex(0xD8D2C8),
        0
    );

    // =====================================================
    // 创建右眼
    // =====================================================

    right_eye = lv_obj_create(lv_scr_act());

    lv_obj_set_size(right_eye, 238, 238);

    lv_obj_center(right_eye);

    lv_obj_set_pos(right_eye, 1, 1);

    lv_obj_set_style_radius(
        right_eye,
        LV_RADIUS_CIRCLE,
        0
    );

    lv_obj_set_style_bg_color(
        right_eye,
        lv_color_hex(0xD8D2C8),
        0
    );

    lv_obj_set_style_bg_opa(
        right_eye,
        LV_OPA_COVER,
        0
    );

    // 去边框

    lv_obj_set_style_border_width(
        right_eye,
        0,
        0
    );

    lv_obj_set_style_outline_width(
        right_eye,
        0,
        0
    );

    lv_obj_set_style_shadow_width(
        right_eye,
        0,
        0
    );

    lv_obj_set_style_pad_all(
        right_eye,
        0,
        0
    );

    lv_obj_clear_flag(
        right_eye,
        LV_OBJ_FLAG_SCROLLABLE
    );

    // =====================================================
    // 创建右瞳孔
    // =====================================================

    right_pupil = lv_obj_create(right_eye);

    lv_obj_set_size(right_pupil, 130, 130);

    lv_obj_set_style_radius(
        right_pupil,
        LV_RADIUS_CIRCLE,
        0
    );

    lv_obj_set_style_bg_color(
        right_pupil,
        lv_color_hex(0x000000),
        0
    );

    lv_obj_set_style_bg_opa(
        right_pupil,
        LV_OPA_COVER,
        0
    );

    lv_obj_set_style_border_width(
        right_pupil,
        0,
        0
    );

    lv_obj_align(
        right_pupil,
        LV_ALIGN_CENTER,
        0,
        0
    );

    vTaskDelay(pdMS_TO_TICKS(100));

}

// =====================================================
// 更新动画
// =====================================================

void Eye_Thinking_Update()
{
    vTaskDelay(pdMS_TO_TICKS(20));

    // =====================================================
    // 圆周运动半径
    // =====================================================

    int radius = 18;

    // =====================================================
    // 计算圆周坐标
    // =====================================================

    int x = radius * cos(angle);

    int y = radius * sin(angle);

    // =====================================================
    // 更新左瞳孔位置
    // =====================================================

    lv_obj_align(
        left_pupil,
        LV_ALIGN_CENTER,
        x,
        y
    );

    // =====================================================
    // 更新右瞳孔位置
    // =====================================================

    lv_obj_align(
        right_pupil,
        LV_ALIGN_CENTER,
        x,
        y
    );

    // =====================================================
    // 增加角度
    // =====================================================

    angle += 0.08;

    // 防止无限增大

    if (angle > 6.28)
    {
        angle = 0;
    }
}