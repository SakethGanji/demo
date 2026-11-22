import os, signal, time
def die():
    os.kill(os.getpid(), signal.SIGKILL)
def slow(s):
    time.sleep(s); return os.getpid()
def ok():
    return os.getpid()
