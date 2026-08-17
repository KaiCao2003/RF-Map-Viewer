#include <errno.h>
#include <stdio.h>
#include <string.h>

#if defined(__APPLE__)

int
main(int argc, char **argv)
{
    unsigned int flags;

    if (argc != 4) {
        fprintf(stderr, "usage: %s --swap|--exclusive FIRST_PATH SECOND_PATH\n", argv[0]);
        return 64;
    }

    if (strcmp(argv[1], "--swap") == 0) {
        flags = RENAME_SWAP;
    } else if (strcmp(argv[1], "--exclusive") == 0) {
        flags = RENAME_EXCL;
    } else {
        fprintf(stderr, "unknown operation: %s\n", argv[1]);
        return 64;
    }

    if (strcmp(argv[2], argv[3]) == 0) {
        fprintf(stderr, "refusing to rename a path onto itself: %s\n", argv[2]);
        return 64;
    }

    if (renamex_np(argv[2], argv[3], flags) != 0) {
        fprintf(
            stderr,
            "renamex_np(%s) failed for '%s' and '%s': %s\n",
            argv[1],
            argv[2],
            argv[3],
            strerror(errno)
        );
        return 1;
    }

    return 0;
}

#else

int
main(void)
{
    fprintf(stderr, "rename_swap_macos is available only on macOS\n");
    return 69;
}

#endif
