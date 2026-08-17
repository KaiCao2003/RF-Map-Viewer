#ifndef RFMAPPING_MACOS_RENAME_API_SHIM_H
#define RFMAPPING_MACOS_RENAME_API_SHIM_H

/*
 * Linux-only syntax-test shim for the small macOS helper. These declarations
 * mirror <stdio.h> on macOS; production builds never include this file.
 */
#define RENAME_SWAP 0x00000002U
#define RENAME_EXCL 0x00000004U

int renamex_np(const char *from, const char *to, unsigned int flags);

#endif
