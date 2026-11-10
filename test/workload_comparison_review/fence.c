#define _GNU_SOURCE
#include <dlfcn.h>
#include <fcntl.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

/* Deterministic read boundary selected independently of B's first-right-only
 * fence: result opens 1/2 left capture, 3/4 right capture, 5/6 left recheck,
 * 7/8 right recheck. No sleeps, mutation only in disposable fixtures. */
int openat(int dir, const char *path, int flags, ...) {
  static int (*real_openat)(int,const char *,int,...) = NULL;
  static int count;
  mode_t mode=0;
  if (flags & O_CREAT) { va_list ap; va_start(ap,flags); mode=va_arg(ap,int); va_end(ap); }
  if (!real_openat) real_openat=dlsym(RTLD_NEXT,"openat");
  if (!strcmp(path,"result.json") && ++count==atoi(getenv("PT8_OPEN_COUNT") ? getenv("PT8_OPEN_COUNT") : "0")) {
    char byte='R';
    if (write(atoi(getenv("PT8_NOTIFY")),&byte,1)!=1 ||
        read(atoi(getenv("PT8_RELEASE")),&byte,1)!=1) _exit(97);
  }
  return real_openat(dir,path,flags,mode);
}
int openat64(int dir, const char *path, int flags, ...) {
  mode_t mode=0;
  if (flags & O_CREAT) { va_list ap; va_start(ap,flags); mode=va_arg(ap,int); va_end(ap); }
  return openat(dir,path,flags,mode);
}
