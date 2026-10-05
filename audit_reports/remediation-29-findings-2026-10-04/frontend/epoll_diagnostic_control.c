#include <errno.h>
#include <stdio.h>
#include <sys/epoll.h>
#include <unistd.h>
int main(void) {
    int epfd = epoll_create1(0);
    struct epoll_event event = {.events = EPOLLIN};
    errno = 0;
    int result = epoll_ctl(epfd, EPOLL_CTL_ADD, -1, &event);
    int saved = errno;
    close(epfd);
    printf("known_bad_fd result=%d errno=%d\n", result, saved);
    return (result == -1 && saved == EBADF) ? 0 : 1;
}
