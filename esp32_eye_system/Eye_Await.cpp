#include "Eye_Await.h"
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

// ===== 左右待机眼对象 =====
static lv_obj_t * left_await_eye;
static lv_obj_t * right_await_eye;

// ===== 呼吸动画参数 =====
static int eye_height = 55;
static bool shrinking = true;

void Eye_Await_Init()
{
    // =====================================================
    // 左屏
    // =====================================================
    lv_disp_set_default(disp);
    lv_obj_clean(lv_scr_act());

    lv_obj_set_style_bg_color(lv_scr_act(), lv_color_hex(0xD8D2C8), 0);

    left_await_eye = lv_obj_create(lv_scr_act());
    lv_obj_set_size(left_await_eye, 210, eye_height);
    lv_obj_set_style_radius(left_await_eye, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(left_await_eye, lv_color_hex(0xB8B8B8), 0);
    lv_obj_set_style_bg_opa(left_await_eye, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(left_await_eye, 0, 0);
    lv_obj_set_style_outline_width(left_await_eye, 0, 0);
    lv_obj_set_style_shadow_width(left_await_eye, 0, 0);
    lv_obj_set_style_pad_all(left_await_eye, 0, 0);
    lv_obj_clear_flag(left_await_eye, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_align(left_await_eye, LV_ALIGN_CENTER, 0, 20);

    // =====================================================
    // 右屏
    // =====================================================
    lv_disp_set_default(disp2);
    lv_obj_clean(lv_scr_act());

    lv_obj_set_style_bg_color(lv_scr_act(), lv_color_hex(0xD8D2C8), 0);

    right_await_eye = lv_obj_create(lv_scr_act());
    lv_obj_set_size(right_await_eye, 210, eye_height);
    lv_obj_set_style_radius(right_await_eye, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(right_await_eye, lv_color_hex(0xB8B8B8), 0);
    lv_obj_set_style_bg_opa(right_await_eye, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(right_await_eye, 0, 0);
    lv_obj_set_style_outline_width(right_await_eye, 0, 0);
    lv_obj_set_style_shadow_width(right_await_eye, 0, 0);
    lv_obj_set_style_pad_all(right_await_eye, 0, 0);
    lv_obj_clear_flag(right_await_eye, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_align(right_await_eye, LV_ALIGN_CENTER, 0, 20);

    vTaskDelay(pdMS_TO_TICKS(100));
}

void Eye_Await_Update()
{
    vTaskDelay(pdMS_TO_TICKS(40));

    // ===== 呼吸动画 =====
    if (shrinking)
    {
        eye_height -= 1;
        if (eye_height <= 42)
            shrinking = false;
    }
    else
    {
        eye_height += 1;
        if (eye_height >= 55)
            shrinking = true;
    }

    // ===== 更新左眼高度 =====
    lv_disp_set_default(disp);
    lv_obj_set_height(left_await_eye, eye_height);

    // ===== 更新右眼高度 =====
    lv_disp_set_default(disp2);
    lv_obj_set_height(right_await_eye, eye_height);
}