"""Exclusive Runtime owner per cache directory; OS releases lock on process exit."""
from contextlib import contextmanager
import os
from pathlib import Path

@contextmanager
def instance_lock(root):
    path=Path(root)/'runtime-owner.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.is_symlink(): raise ValueError('Symlink instance lock is not allowed')
    handle=path.open('a+b')
    try:
        handle.seek(0,2)
        if handle.tell()==0:handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError('另一个 Model Runtime 正在使用该缓存；请先退出旧 Runtime，不会改写活动任务') from error
        try:yield
        finally:
            handle.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(),fcntl.LOCK_UN)
    finally:handle.close()
