#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Pipe rendezvous only. Python mutates a disposable fixture while the actual
 * reader is stopped exactly at its openat boundary. */
static void barrier(void)
{
  char byte = 'R';
  int notify = atoi(getenv("VARIANT_REVIEW_NOTIFY_FD"));
  int release = atoi(getenv("VARIANT_REVIEW_RELEASE_FD"));
  if (write(notify, &byte, 1) != 1 || read(release, &byte, 1) != 1) _exit(97);
}

int openat(int directory, const char *path, int flags, ...)
{
  static int (*real_openat)(int, const char *, int, ...) = NULL;
  static int stopped = 0;
  mode_t mode = 0;
  if (flags & O_CREAT) {
    va_list args;
    va_start(args, flags); mode = va_arg(args, int); va_end(args);
  }
  if (!real_openat) real_openat = dlsym(RTLD_NEXT, "openat");
  int chosen = !stopped && !strcmp(path, "results.json") && getenv("VARIANT_REVIEW_NOTIFY_FD");
  if (chosen) stopped = 1;
  if (chosen && !strcmp(getenv("VARIANT_REVIEW_FENCE"), "before")) barrier();
  int result = real_openat(directory, path, flags, mode);
  if (chosen && !strcmp(getenv("VARIANT_REVIEW_FENCE"), "after")) barrier();
  return result;
}

/* OCaml's C flags select the large-file libc spelling on this switch. */
int openat64(int directory, const char *path, int flags, ...)
{
  mode_t mode = 0;
  if (flags & O_CREAT) {
    va_list args;
    va_start(args, flags); mode = va_arg(args, int); va_end(args);
  }
  return openat(directory, path, flags, mode);
}
