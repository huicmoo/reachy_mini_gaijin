#include "Eye_Normal.h"
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

// ===== 左右眼对象 =====
static lv_obj_t * left_eye;
static lv_obj_t * right_eye;

static lv_obj_t * left_pupil;
static lv_obj_t * right_pupil;

// ===== 眨眼参数 =====
static int eye_height = 220;
static bool closing = false;
static unsigned long lastBlink = 0;

void Eye_Normal_Init()
{
    // =====================================================
    // 左屏
    // =====================================================
    lv_disp_set_default(disp);
    lv_obj_clean(lv_scr_act());

    // 背景颜色
    lv_obj_set_style_bg_color(lv_scr_act(), lv_color_hex(0xD8D2C8), 0);

    // 左眼
    left_eye = lv_obj_create(lv_scr_act());
    lv_obj_set_size(left_eye, 232, 232);
    lv_obj_set_style_radius(left_eye, LV_RADIUS_CIRCLE, 0);
    lv_obj_clear_flag(left_eye, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(left_eye, lv_color_hex(0xD8D2C8), 0);
    lv_obj_center(left_eye);
    lv_obj_set_pos(left_eye, 1, 1);
    lv_obj_set_style_border_width(left_eye, 0, 0);
    lv_obj_set_style_pad_all(left_eye, 0, 0);
    lv_obj_set_style_outline_width(left_eye, 0, 0);
    lv_obj_set_style_shadow_width(left_eye, 0, 0);
    lv_obj_set_style_opa(left_eye, LV_OPA_COVER, 0);

    // 左瞳孔
    left_pupil = lv_obj_create(left_eye);
    lv_obj_set_size(left_pupil, 150, 150);
    lv_obj_set_style_radius(left_pupil, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(left_pupil, lv_color_hex(0x000000), 0);
    lv_obj_set_style_border_width(left_pupil, 0, 0);
    lv_obj_align(left_pupil, LV_ALIGN_CENTER, 20, 0);

    // =====================================================
    // 右屏
    // =====================================================
    lv_disp_set_default(disp2);
    lv_obj_clean(lv_scr_act());

    lv_obj_set_style_bg_color(lv_scr_act(), lv_color_hex(0xD8D2C8), 0);

    right_eye = lv_obj_create(lv_scr_act());
    lv_obj_set_size(right_eye, 232, 232);
    lv_obj_set_style_radius(right_eye, LV_RADIUS_CIRCLE, 0);
    lv_obj_clear_flag(right_eye, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_bg_color(right_eye, lv_color_hex(0xD8D2C8), 0);
    lv_obj_center(right_eye);
    lv_obj_set_pos(right_eye, 1, 1);
    lv_obj_set_style_border_width(right_eye, 0, 0);
    lv_obj_set_style_pad_all(right_eye, 0, 0);
    lv_obj_set_style_outline_width(right_eye, 0, 0);
    lv_obj_set_style_shadow_width(right_eye, 0, 0);
    lv_obj_set_style_opa(right_eye, LV_OPA_COVER, 0);

    right_pupil = lv_obj_create(right_eye);
    lv_obj_set_size(right_pupil, 150, 150);
    lv_obj_set_style_radius(right_pupil, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(right_pupil, lv_color_hex(0x000000), 0);
    lv_obj_set_style_border_width(right_pupil, 0, 0);
    lv_obj_align(right_pupil, LV_ALIGN_CENTER, -20, 0);

    vTaskDelay(pdMS_TO_TICKS(100));
}

void Eye_Normal_Update()
{
    vTaskDelay(pdMS_TO_TICKS(5));

    // ===== 每4.5秒眨眼 =====
    if (millis() - lastBlink > 4500)
    {
        closing = true;
        lastBlink = millis();
    }

    // ===== 闭眼 =====
    if (closing)
    {
        eye_height -= 8;
        if (eye_height <= 5)
            closing = false;
    }
    else // 睁眼
    {
        if (eye_height < 220)
            eye_height += 8;
    }

    // ===== 更新左眼 =====
    lv_disp_set_default(disp);
    lv_obj_set_height(left_eye, eye_height);

    // ===== 更新右眼 =====
    lv_disp_set_default(disp2);
    lv_obj_set_height(right_eye, eye_height);
}