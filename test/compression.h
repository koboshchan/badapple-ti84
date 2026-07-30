/**
 * Host stub for the CE toolchain's compression.h.
 *
 * zx0 expansion exists only as eZ80 code, so it cannot run on the host. Host
 * tests therefore use the uncompressed copy of the payload that util/encode.py
 * writes alongside the compressed one; reaching this function means something
 * pointed hosttest at compressed data, so it fails loudly rather than silently
 * producing garbage.
 */

#ifndef HOST_COMPRESSION_H
#define HOST_COMPRESSION_H

#include <stdio.h>
#include <stdlib.h>

static inline void zx0_Decompress(void *dst, const void *src)
{
    (void)dst;
    (void)src;
    fprintf(stderr, "hosttest: zx0 expansion is not available on the host; "
                    "use the encoder's uncompressed copy\n");
    exit(2);
}

#endif /* HOST_COMPRESSION_H */
