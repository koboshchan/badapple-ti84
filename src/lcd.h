/**
 * 1bpp LCD mode for full-screen monochrome video playback.
 *
 * The CE's PL111 controller can scan out 1 bit per pixel through a two-entry
 * palette, so the whole 320x240 screen is only 9600 bytes and every byte is
 * eight finished pixels.
 *
 * Video is encoded at the panel's native size, so decoded bytes are written
 * straight into VRAM: no pixel expansion, no blitting, no second buffer. That is
 * what lets the player keep up.
 *
 * Setting FRAME_WIDTH/FRAME_HEIGHT to 160/120 and FRAME_SCALE to 2 (and WIDTH,
 * HEIGHT and SCALE to match in util/badapple.py) switches to half-resolution
 * video that the player pixel-doubles onto the screen, which quarters the data a
 * given framerate needs.
 */

#ifndef BADAPPLE_LCD_H
#define BADAPPLE_LCD_H

#include <stdbool.h>
#include <stdint.h>

/* Encoded video geometry. Must match WIDTH and HEIGHT in util/badapple.py; a
 * file that disagrees is rejected rather than mis-rendered. */
#define FRAME_WIDTH 320
#define FRAME_HEIGHT 240
#define FRAME_ROW_BYTES (FRAME_WIDTH / 8)             /* 40 */
#define FRAME_BYTES (FRAME_ROW_BYTES * FRAME_HEIGHT)  /* 9600 */

/* How much the player scales up to fill the panel. 1 or 2. */
#define FRAME_SCALE 1

/* The panel itself. */
#define DISPLAY_WIDTH (FRAME_WIDTH * FRAME_SCALE)          /* 320 */
#define DISPLAY_HEIGHT (FRAME_HEIGHT * FRAME_SCALE)        /* 240 */
#define DISPLAY_ROW_BYTES (DISPLAY_WIDTH / 8)              /* 40 */
#define DISPLAY_BYTES (DISPLAY_ROW_BYTES * DISPLAY_HEIGHT) /* 9600 */

/**
 * Byte offset into VRAM of memory free for other uses.
 *
 * The 1bpp screen occupies only the first 9600 bytes of the 153600-byte video
 * RAM, so everything past this offset is scratch space the display never reads.
 */
#define LCD_SCRATCH_OFFSET 32768

/** Switches the LCD into 1bpp mode.
 *
 * @param msb_first true if the leftmost pixel of a byte is bit 7 rather than
 *                  bit 0; must match the encoder's --bit-order.
 */
void lcd_Begin(bool msb_first);

/** Restores the LCD to the mode the OS was using. */
void lcd_End(void);

/**
 * Returns the buffer frames should be decoded into.
 *
 * At scale 1 this is VRAM itself, so decoding writes finished pixels with no
 * copy at all. At scale 2 it is an off-screen buffer that lcd_Present expands.
 */
uint8_t *lcd_FrameBuffer(void);

/**
 * Puts the decoded frame on screen.
 *
 * Nothing to do at scale 1, where decoding already wrote into VRAM. Only needs
 * calling when the frame actually changed.
 */
void lcd_Present(void);

/**
 * Returns scratch memory inside VRAM that the display never reads.
 *
 * Valid whether or not lcd_Begin has run, and good for at least
 * LCD_SIZE - LCD_SCRATCH_OFFSET bytes.
 */
uint8_t *lcd_Scratch(void);

#endif /* BADAPPLE_LCD_H */
