#include "lcd.h"

#include <string.h>
#include <sys/lcd.h>

/* PL111 LCD control register fields. */
#define LCD_CTRL_BPP_SHIFT 1
#define LCD_CTRL_BPP_MASK (0x7 << LCD_CTRL_BPP_SHIFT)
#define LCD_CTRL_BPP_1 (0 << LCD_CTRL_BPP_SHIFT)
#define LCD_CTRL_BEBO (1 << 9)  /* big-endian byte order */
#define LCD_CTRL_BEPO (1 << 10) /* big-endian pixel order within a byte */

static uint24_t saved_control;
static uint24_t saved_upbase;
static uint16_t saved_palette[2];
static bool active;

uint8_t *lcd_Begin(bool msb_first)
{
    saved_control = lcd_Control;
    saved_upbase = lcd_UpBase;
    saved_palette[0] = lcd_Palette[0];
    saved_palette[1] = lcd_Palette[1];
    active = true;

    /* Palette entry 0 is a clear pixel, entry 1 a set pixel. An inverted
     * encode is handled by the encoder flipping the pixels, so nothing to do
     * here for the header's invert flag. */
    lcd_Palette[0] = 0x0000;
    lcd_Palette[1] = 0xFFFF;

    memset(lcd_Ram, 0, FRAME_BYTES);
    lcd_UpBase = (uint24_t)lcd_Ram;

    /* Keep the OS's TFT/BGR/power bits; change only the depth and, if the data
     * was packed the other way round, the pixel order. */
    uint24_t control = (saved_control & ~(uint24_t)LCD_CTRL_BPP_MASK) |
                       LCD_CTRL_BPP_1;
    control &= ~(uint24_t)(LCD_CTRL_BEBO | LCD_CTRL_BEPO);
    if (msb_first) {
        control |= LCD_CTRL_BEPO;
    }
    lcd_Control = control;

    return (uint8_t *)lcd_Ram;
}

uint8_t *lcd_Scratch(void)
{
    return (uint8_t *)lcd_Ram + LCD_SCRATCH_OFFSET;
}

void lcd_End(void)
{
    if (!active) {
        return;
    }
    active = false;
    lcd_Control = saved_control;
    lcd_UpBase = saved_upbase;
    lcd_Palette[0] = saved_palette[0];
    lcd_Palette[1] = saved_palette[1];
    /* The OS expects to own a 16bpp screen; leave it blank rather than showing
     * the 1bpp frame data reinterpreted as colour. */
    memset(lcd_Ram, 0xFF, LCD_SIZE);
}
