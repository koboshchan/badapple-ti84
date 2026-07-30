/**
 * Host stub for the CE's fileioc library.
 *
 * Lets src/video.c compile and run on a desktop against chunk .bin files, so
 * the decoder that actually ships can be tested without a calculator. Each
 * "variable" is a file in the data directory; see test/hosttest.c.
 */

#ifndef HOST_FILEIOC_H
#define HOST_FILEIOC_H

#include <stdbool.h>
#include <stdint.h>

/** Loads DATADIR so the stub can find chunk files. */
void host_SetDataDir(const char *dir);

uint8_t ti_Open(const char *name, const char *mode);
void ti_Close(uint8_t handle);
void *ti_GetDataPtr(uint8_t handle);
uint16_t ti_GetSize(uint8_t handle);
int ti_IsArchived(uint8_t handle);
int ti_SetArchiveStatus(bool archive, uint8_t handle);

#endif /* HOST_FILEIOC_H */
