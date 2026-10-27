"""Small standalone PTY bootstrap, executed before applying the shell sandbox.

Runs in a fresh process, so ioctl/exec do not use a Python preexec callback in
our threaded API process. The caller already created a new session and mapped
its PTY slave onto stdin/out/err. macOS requires explicit TIOCSCTTY.
"""
import os
import sys


def main():
    import fcntl
    import termios
    if sys.platform != 'darwin' or len(sys.argv) < 2 or sys.argv[1] != '/usr/bin/sandbox-exec':
        raise SystemExit(2)
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    os.tcsetpgrp(0, os.getpgrp())
    os.execve(sys.argv[1], sys.argv[1:], dict(os.environ))


if __name__ == '__main__':
    main()
