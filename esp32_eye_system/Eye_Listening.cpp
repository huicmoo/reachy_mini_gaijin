#include "Eye_Listening.h"

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
// 左右眼对象
// =====================================================
static lv_obj_t * left_eye;
static lv_obj_t * right_eye;

static lv_obj_t * left_pupil;
static lv_obj_t * right_pupil;

// =====================================================
// 眼球位置
// =====================================================
static int current_offset = -28;
static int target_offset = 28;

// =====================================================
// 动作状态
// 0 = 左→右, 1 = 回中, 2 = 眨眼, 3 = 回左
// =====================================================
static int state = 0;

// =====================================================
// 眨眼参数
// =====================================================
static int eye_height = 230;
static bool blinking = false;

// =====================================================
// 初始化函数
// =====================================================
void Eye_Listening_Init()
{
    // =====================================================
    // 左屏
    // =====================================================
    lv_disp_set_default(disp);
    lv_obj_clean(lv_scr_act());
    lv_obj_set_style_bg_color(lv_scr_act(), lv_color_hex(0xD8D2C8), 0);

    left_eye = lv_obj_create(lv_scr_act());
    lv_obj_set_size(left_eye, 232, 232);
    lv_obj_center(left_eye);
    lv_obj_set_pos(left_eye, 1, 1);
    lv_obj_set_style_bg_color(left_eye, lv_color_hex(0xD8D2C8), 0);
    lv_obj_set_style_bg_opa(left_eye, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(left_eye, 0, 0);
    lv_obj_set_style_outline_width(left_eye, 0, 0);
    lv_obj_set_style_shadow_width(left_eye, 0, 0);
    lv_obj_set_style_pad_all(left_eye, 0, 0);
    lv_obj_clear_flag(left_eye, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_radius(left_eye, LV_RADIUS_CIRCLE, 0);

    left_pupil = lv_obj_create(left_eye);
    lv_obj_set_size(left_pupil, 170, 170);
    lv_obj_set_style_radius(left_pupil, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(left_pupil, lv_color_hex(0x000000), 0);
    lv_obj_set_style_bg_opa(left_pupil, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(left_pupil, 0, 0);

    // =====================================================
    // 右屏
    // =====================================================
    lv_disp_set_default(disp2);
    lv_obj_clean(lv_scr_act());
    lv_obj_set_style_bg_color(lv_scr_act(), lv_color_hex(0xD8D2C8), 0);

    right_eye = lv_obj_create(lv_scr_act());
    lv_obj_set_size(right_eye, 232, 232);
    lv_obj_center(right_eye);
    lv_obj_set_pos(right_eye, 1, 1);
    lv_obj_set_style_bg_color(right_eye, lv_color_hex(0xD8D2C8), 0);
    lv_obj_set_style_bg_opa(right_eye, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(right_eye, 0, 0);
    lv_obj_set_style_outline_width(right_eye, 0, 0);
    lv_obj_set_style_shadow_width(right_eye, 0, 0);
    lv_obj_set_style_pad_all(right_eye, 0, 0);
    lv_obj_clear_flag(right_eye, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_style_radius(right_eye, LV_RADIUS_CIRCLE, 0);

    right_pupil = lv_obj_create(right_eye);
    lv_obj_set_size(right_pupil, 170, 170);
    lv_obj_set_style_radius(right_pupil, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_color(right_pupil, lv_color_hex(0x000000), 0);
    lv_obj_set_style_bg_opa(right_pupil, LV_OPA_COVER, 0);
    lv_obj_set_style_border_width(right_pupil, 0, 0);

    vTaskDelay(pdMS_TO_TICKS(100));
}

// =====================================================
// 更新函数
// =====================================================
void Eye_Listening_Update()
{
    vTaskDelay(pdMS_TO_TICKS(12));

    // ===== 状态0：左→右 =====
    if (state == 0)
    {
        target_offset = 28;
        if (current_offset < target_offset)
            current_offset += 1;
        else
            state = 1;
    }
    // ===== 状态1：右→中 =====
    else if (state == 1)
    {
        target_offset = 0;
        if (current_offset > target_offset)
            current_offset -= 1;
        else
        {
            state = 2;
            blinking = true;
        }
    }
    // ===== 状态2：眨眼 =====
    else if (state == 2)
    {
        if (blinking)
        {
            eye_height -= 5;
            if (eye_height <= 20)
                blinking = false;
        }
        else
        {
            if (eye_height < 230)
                eye_height += 5;
            else
            {
                target_offset = -28;
                state = 3;
            }
        }
    }
    // ===== 状态3：缓慢回左边 =====
    else if (state == 3)
    {
        if (current_offset > target_offset)
            current_offset -= 1;
        else
            state = 0;
    }

    // 更新瞳孔位置
    lv_obj_align(left_pupil, LV_ALIGN_CENTER, current_offset, 0);
    lv_obj_align(right_pupil, LV_ALIGN_CENTER, current_offset, 0);

    // 更新眼睛高度
    lv_disp_set_default(disp);
    lv_obj_set_height(left_eye, eye_height);
    lv_disp_set_default(disp2);
    lv_obj_set_height(right_eye, eye_height);
}