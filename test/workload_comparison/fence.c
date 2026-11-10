#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Stop at the right workload's first read, after both left snapshot replays.
   No sleeps: parent mutates disposable retained bytes and releases the reader. */
int openat(int directory, const char *path, int flags, ...)
{
  static int (*real_openat)(int, const char *, int, ...) = NULL;
  static int count = 0;
  mode_t mode = 0;
  if (flags & O_CREAT) {
    va_list args;
    va_start(args, flags); mode = va_arg(args, int); va_end(args);
  }
  if (!real_openat) real_openat = dlsym(RTLD_NEXT, "openat");
  int chosen = getenv("PT8_OPEN_COUNT") ? atoi(getenv("PT8_OPEN_COUNT")) : 3;
  if (!strcmp(path, "result.json") && ++count == chosen && getenv("PT8_NOTIFY")) {
    char byte = 'R';
    if (write(atoi(getenv("PT8_NOTIFY")), &byte, 1) != 1 ||
        read(atoi(getenv("PT8_RELEASE")), &byte, 1) != 1) _exit(97);
  }
  return real_openat(directory, path, flags, mode);
}

int openat64(int directory, const char *path, int flags, ...)
{
  mode_t mode = 0;
  if (flags & O_CREAT) {
    va_list args;
    va_start(args, flags); mode = va_arg(args, int); va_end(args);
  }
  return openat(directory, path, flags, mode);
}
