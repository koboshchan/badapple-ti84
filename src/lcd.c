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

#if FRAME_SCALE == 2
/* Each source byte is eight pixels, which double into sixteen: two output bytes.
 * expand_lo holds the left half, expand_hi the right. */
static uint8_t expand_lo[256];
static uint8_t expand_hi[256];

/* Frames are decoded here, then expanded onto the screen by lcd_Present. */
static uint8_t offscreen[FRAME_BYTES];

/**
 * Fills the doubling table for the configured pixel order.
 *
 * Pixels are numbered left to right; which bit of a byte that is depends on the
 * order the encoder packed them in.
 */
static void build_expand_table(bool msb_first)
{
    for (unsigned int value = 0; value < 256; value++) {
        uint8_t lo = 0;
        uint8_t hi = 0;
        for (unsigned int pixel = 0; pixel < 8; pixel++) {
            unsigned int src_bit = msb_first ? (7 - pixel) : pixel;
            if (!(value & (1u << src_bit))) {
                continue;
            }
            /* This pixel becomes the pair at 2*pixel and 2*pixel+1. */
            for (unsigned int half = 0; half < 2; half++) {
                unsigned int out = pixel * 2 + half;
                unsigned int bit = msb_first ? (7 - (out & 7)) : (out & 7);
                if (out < 8) {
                    lo |= (uint8_t)(1u << bit);
                } else {
                    hi |= (uint8_t)(1u << bit);
                }
            }
        }
        expand_lo[value] = lo;
        expand_hi[value] = hi;
    }
}

uint8_t *lcd_FrameBuffer(void)
{
    return offscreen;
}

void lcd_Present(void)
{
    const uint8_t *frame = offscreen;
    uint8_t *dst = (uint8_t *)lcd_Ram;

    for (unsigned int row = 0; row < FRAME_HEIGHT; row++) {
        uint8_t *out = dst;
        for (unsigned int col = 0; col < FRAME_ROW_BYTES; col++) {
            uint8_t value = frame[col];
            *out++ = expand_lo[value];
            *out++ = expand_hi[value];
        }
        /* Vertical doubling: the row was just built, so copy it down whole. */
        memcpy(dst + DISPLAY_ROW_BYTES, dst, DISPLAY_ROW_BYTES);
        dst += 2 * DISPLAY_ROW_BYTES;
        frame += FRAME_ROW_BYTES;
    }
}
#else
uint8_t *lcd_FrameBuffer(void)
{
    /* Frames are decoded straight into the scanned-out buffer. */
    return (uint8_t *)lcd_Ram;
}

void lcd_Present(void)
{
    /* Already on screen. */
}
#endif

void lcd_Begin(bool msb_first)
{
#if FRAME_SCALE == 2
    build_expand_table(msb_first);
    memset(offscreen, 0, sizeof offscreen);
#endif

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

    memset(lcd_Ram, 0, DISPLAY_BYTES);
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
