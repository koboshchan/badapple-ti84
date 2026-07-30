/**
 * Bad Apple for the TI-84 Plus CE.
 *
 * Plays a full-screen 320x240 monochrome video streamed straight out of the
 * flash archive. See the README for the build pipeline and data format.
 */

#include <keypadc.h>
#include <stdio.h>
#include <sys/timers.h>
#include <ti/getcsc.h>
#include <ti/screen.h>

#include "lcd.h"
#include "video.h"

#define TIMER_HZ 32768UL

static uint24_t saved_timer_control;

/** Starts timer 1 counting up at 32768 Hz, used as the playback clock. */
static void clock_Begin(void)
{
    saved_timer_control = timer_Control;
    timer_Disable(1);
    timer_Set(1, 0);
    timer_SetReload(1, 0xFFFFFFFF);
    timer_Enable(1, TIMER_32K, TIMER_NOINT, TIMER_UP);
}

static void clock_End(void)
{
    timer_Disable(1);
    timer_Control = saved_timer_control;
}

int main(void)
{
    video_info_t info;
    /* Compressed blocks are expanded into spare VRAM, which the display does not
     * read in 1bpp mode, so the block buffer costs the program no RAM. */
    video_status_t status = video_Open(&info, lcd_Scratch());
    if (status != VIDEO_OK) {
        os_ClrHome();
        printf("Bad Apple CE\n\n%s\n", video_StatusText(status));
        while (!os_GetCSC()) {
        }
        return 1;
    }

    lcd_Begin(info.msb_first);
    uint8_t *frame = lcd_FrameBuffer();
    clock_Begin();

    unsigned long dropped = 0;
    unsigned int shown = 0;
    bool aborted = false;

    for (;;) {
        status = video_NextFrame(frame);
        if (status == VIDEO_OK) {
            lcd_Present();
        } else if (status != VIDEO_UNCHANGED) {
            break;
        }
        shown++;

        /* Compute each deadline from the frame index rather than by adding a
         * per-frame interval, so rounding never accumulates into drift. */
        uint32_t deadline = (uint32_t)((unsigned long)shown * TIMER_HZ /
                                      info.fps);
        if (timer_Get(1) > deadline) {
            dropped++;  /* decode overran its slot; play on without waiting */
        } else {
            while (timer_Get(1) < deadline) {
            }
        }

        kb_Scan();
        if (kb_Data[6] & kb_Clear) {
            aborted = true;
            break;
        }
    }

    clock_End();
    lcd_End();
    os_ClrHome();

    if (status == VIDEO_BAD_STREAM) {
        printf("Bad Apple CE\n\n%s\nafter %u frames\n",
               video_StatusText(status), shown);
    } else {
        printf("Bad Apple CE\n\n%u of %u frames%s\n", shown, info.frame_count,
               aborted ? " (stopped)" : "");
        printf("%u fps, %u appvars\n", info.fps, info.chunk_count);
        if (dropped) {
            printf("%lu frames ran late\n", dropped);
        }
    }
    while (!os_GetCSC()) {
    }
    return 0;
}
