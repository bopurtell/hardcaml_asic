#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* A deterministic rendezvous at a chosen retained-file open. Selection occurs
   before or after the real descriptor open; no timing-based race assumptions. */
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
  int chosen = getenv("REVIEW_MEMBER") && !strcmp(path, getenv("REVIEW_MEMBER")) &&
    ++count == atoi(getenv("REVIEW_NTH"));
  int result;
  if (chosen && !strcmp(getenv("REVIEW_WHEN"), "before")) {
    char b = 'R';
    if (write(atoi(getenv("REVIEW_NOTIFY")), &b, 1) != 1 ||
        read(atoi(getenv("REVIEW_RELEASE")), &b, 1) != 1) _exit(97);
  }
  result = real_openat(directory, path, flags, mode);
  if (chosen && !strcmp(getenv("REVIEW_WHEN"), "after")) {
    char b = 'R';
    if (write(atoi(getenv("REVIEW_NOTIFY")), &b, 1) != 1 ||
        read(atoi(getenv("REVIEW_RELEASE")), &b, 1) != 1) _exit(97);
  }
  return result;
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
