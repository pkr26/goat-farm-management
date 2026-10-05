/* Read-only epoll failure observation. Never changes a syscall result or errno.
 * Built only in a disposable diagnostic container, never in production images.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <errno.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <sys/epoll.h>
#include <sys/syscall.h>
#include <unistd.h>

static int (*real_epoll_ctl)(int, int, int, struct epoll_event *);
static pthread_once_t once = PTHREAD_ONCE_INIT;
static void resolve_epoll(void) {
    real_epoll_ctl = dlsym(RTLD_NEXT, "epoll_ctl");
    if (!real_epoll_ctl) { fputs("EPOLL_DIAGNOSTIC_RESOLVE_FAILED\n", stderr); _exit(125); }
}

int epoll_ctl(int epfd, int op, int fd, struct epoll_event *event) {
    int before = errno;
    pthread_once(&once, resolve_epoll);
    errno = before;
    int result = real_epoll_ctl(epfd, op, fd, event);
    int saved_errno = errno;
    if (result != 0 && op == EPOLL_CTL_ADD && saved_errno != EEXIST) {
        char path[64], target[256] = {0}, ep_target[256] = {0};
        snprintf(path, sizeof(path), "/proc/self/fd/%d", fd);
        ssize_t n = readlink(path, target, sizeof(target)-1);
        if (n < 0) snprintf(target, sizeof(target), "<readlink errno=%d>", errno);
        snprintf(path, sizeof(path), "/proc/self/fd/%d", epfd);
        n = readlink(path, ep_target, sizeof(ep_target)-1);
        if (n < 0) snprintf(ep_target, sizeof(ep_target), "<readlink errno=%d>", errno);
        fprintf(stderr, "EPOLL_DIAGNOSTIC pid=%d tid=%ld epfd=%d(%s) op=%d fd=%d(%s) events=%u result=%d errno=%d\n", getpid(), syscall(SYS_gettid), epfd, ep_target, op, fd, target, event ? event->events : 0, result, saved_errno);
    }
    errno = saved_errno;
    return result;
}
