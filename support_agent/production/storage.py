"""No index writes, automatic recovery or model access in readiness checks."""
from pathlib import Path
import os
import sqlite3
import stat
import threading
import time
from ..documents import local_path
from ..embeddings import PINNED_MODEL, PINNED_DIGEST, config_digest
from ..integrity import checked_index
from ..store import connect
from .config import Rejected

def identity(path):
    s=path.lstat()
    return (s.st_dev,s.st_ino)

def directory(path,uid,gid,*,permissions=True):
    if not path.is_absolute(): raise Rejected('absolute_directory_required')
    local_path(path)
    s=path.lstat()
    if not stat.S_ISDIR(s.st_mode): raise Rejected('invalid_directory')
    if permissions and (s.st_uid!=uid or s.st_gid!=gid or stat.S_IMODE(s.st_mode)!=0o700):
        raise Rejected('directory_permissions')
    return identity(path)

class Guard:
    def __init__(self,config,*,permissions=True):
        self.config,self.permissions=config,permissions
        if permissions and (os.name!='posix' or os.getuid()!=config.uid or os.getgid()!=config.gid or config.uid<1000 or config.gid<1):
            raise Rejected('unsupported_process_identity')
        self.parent=directory(config.data_dir,config.uid,config.gid,permissions=permissions)
        self.pinned=None; self.socket_parent=None; self.socket_id=None
        if config.database.exists() or config.database.is_symlink(): self.pinned=self.file()
        if config.mode=='uds': self.socket_parent=directory(config.relay_dir,config.uid,config.gid,permissions=permissions)
        self.lock=threading.Lock()

    def file(self):
        path=self.config.database
        local_path(path)
        s=path.lstat()
        if not stat.S_ISREG(s.st_mode) or s.st_nlink!=1 or s.st_size>64_000_000: raise Rejected('invalid_index_file')
        if self.permissions and (s.st_uid!=self.config.uid or s.st_gid!=self.config.gid or stat.S_IMODE(s.st_mode)!=0o600):
            raise Rejected('index_permissions')
        for ending in ('-journal','-wal','-shm'):
            p=Path(str(path)+ending)
            if p.exists() or p.is_symlink(): raise Rejected('index_recovery_required')
        return identity(path)

    def route(self):
        if self.config.mode=='disabled': return False,'model_disabled'
        c=self.config
        if directory(c.relay_dir,c.uid,c.gid,permissions=self.permissions)!=self.socket_parent: raise Rejected('relay_directory_changed')
        local_path(c.socket)
        s=c.socket.lstat()
        if not stat.S_ISSOCK(s.st_mode) or self.permissions and (s.st_uid!=c.uid or s.st_gid!=c.gid or stat.S_IMODE(s.st_mode)!=0o600):
            raise Rejected('invalid_relay_socket')
        current=identity(c.socket)
        if self.socket_id is None: self.socket_id=current
        if current!=self.socket_id: raise Rejected('relay_socket_changed')
        return True,'ready'

    def check(self):
        if not self.lock.acquire(blocking=False): return False,'index_check_busy'
        try:
            c=self.config
            if directory(c.data_dir,c.uid,c.gid,permissions=self.permissions)!=self.parent: raise Rejected('data_directory_changed')
            if self.pinned is None: raise Rejected('index_missing_restart_required')
            if self.file()!=self.pinned: raise Rejected('index_replaced_restart_required')
            deadline=time.monotonic()+2
            with connect(c.database) as db:
                db.execute('PRAGMA busy_timeout=100')
                db.set_progress_handler(lambda:int(time.monotonic()>=deadline),100)
                rows,dimension=checked_index(db,c.settings(),allow_empty=True)
                if db.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise Rejected('index_integrity')
                meta=dict(db.execute('SELECT key,value FROM metadata'))
                if db.execute('PRAGMA journal_mode').fetchone()[0]!='delete': raise Rejected('delete_journal_required')
                if dimension!=768 or meta.get('embed_model')!=PINNED_MODEL or meta.get('embed_digest')!=PINNED_DIGEST or meta.get('embed_config')!=config_digest(c.settings()):
                    raise Rejected('index_identity_mismatch')
            if self.file()!=self.pinned: raise Rejected('index_replaced_restart_required')
            return self.route()
        except Rejected as exc: return False,str(exc)
        except (ValueError,RuntimeError,OSError,sqlite3.Error): return False,'index_or_route_unavailable'
        finally: self.lock.release()
