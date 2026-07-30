/**
 * 1bpp LCD mode for full-speed monochrome video playback.
 *
 * The CE's PL111 controller can scan out 1 bit per pixel through a two-entry
 * palette. In that mode a 320x240 frame is 9600 bytes and each decoded byte is
 * eight finished pixels, so frame data goes straight into VRAM with no pixel
 * expansion or blitting at all. That is the whole reason this player can keep
 * up with the video.
 */

#ifndef BADAPPLE_LCD_H
#define BADAPPLE_LCD_H

#include <stdbool.h>
#include <stdint.h>

#define FRAME_WIDTH 320
#define FRAME_HEIGHT 240
#define FRAME_ROW_BYTES (FRAME_WIDTH / 8)               /* 40 */
#define FRAME_BYTES (FRAME_ROW_BYTES * FRAME_HEIGHT)    /* 9600 */

/**
 * Byte offset into VRAM of memory free for other uses.
 *
 * A 1bpp frame occupies only the first 9600 bytes of the 153600-byte video RAM,
 * so everything past this offset is scratch space the display never reads.
 */
#define LCD_SCRATCH_OFFSET 32768

/**
 * Switches the LCD into 1bpp mode and returns the frame buffer to decode into.
 *
 * @param msb_first true if the leftmost pixel of a byte is bit 7 rather than
 *                  bit 0; must match the encoder's --bit-order.
 * @returns The 9600-byte frame buffer being scanned out.
 */
uint8_t *lcd_Begin(bool msb_first);

/** Restores the LCD to the mode the OS was using. */
void lcd_End(void);

/**
 * Returns scratch memory inside VRAM that the display never reads.
 *
 * Valid whether or not lcd_Begin has run, and good for at least
 * LCD_SIZE - LCD_SCRATCH_OFFSET bytes.
 */
uint8_t *lcd_Scratch(void);

#endif /* BADAPPLE_LCD_H */
