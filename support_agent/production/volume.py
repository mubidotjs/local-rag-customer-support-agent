"""Explicit stopped-service backup/restore CLI; never contacts models."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
from .config import Config,Rejected
from .storage import Guard,directory,identity
from ..integrity import checked_index
from ..store import connect

def sha(path):
    value=hashlib.sha256(); total=0
    with path.open('rb') as stream:
        while block:=stream.read(65536):
            total+=len(block)
            if total>64_000_000: raise Rejected('index_size_limit')
            value.update(block)
    return value.hexdigest()

def copy_bounded(source,target):
    total=0
    while block:=source.read(65536):
        total+=len(block)
        if total>64_000_000: raise Rejected('index_size_limit')
        target.write(block)

def inspect(config):
    guard=Guard(config)
    if guard.pinned is None: raise Rejected('index_missing')
    with connect(config.database) as db:
        rows,dimension=checked_index(db,config.settings(),allow_empty=True)
        if db.execute('PRAGMA journal_mode').fetchone()[0]!='delete': raise Rejected('delete_journal_required')
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise Rejected('index_integrity')
    if guard.file()!=guard.pinned: raise Rejected('index_changed')
    return {'sha256':sha(config.database),'chunks':len(rows),'dimension':dimension}

def backup(config,destination):
    directory(destination,config.uid,config.gid)
    if any(destination.iterdir()): raise Rejected('empty_backup_directory_required')
    before=inspect(config); target=destination/'support.sqlite3'
    # Operator must stop all writers. A byte-identical copy plus revalidation
    # detects mutation; incomplete copies remain explicitly unmanifested.
    with target.open('xb') as stream:
        os.chmod(target,0o600)
        with config.database.open('rb') as source: copy_bounded(source,stream)
        stream.flush(); os.fsync(stream.fileno())
    if inspect(config)!=before: raise Rejected('index_changed_during_backup')
    if inspect(Config(data_dir=destination,uid=config.uid,gid=config.gid))!=before: raise Rejected('backup_validation')
    with (destination/'backup.json').open('x',encoding='utf-8') as stream:
        json.dump(before,stream); stream.flush(); os.fsync(stream.fileno())
    os.chmod(destination/'backup.json',0o600)
    return before

def restore(config,source,confirmation):
    if confirmation!='REPLACE-STOPPED-INDEX': raise Rejected('explicit_restore_confirmation_required')
    directory(source,config.uid,config.gid)
    manifest=source/'backup.json'
    from ..documents import local_path
    local_path(manifest)
    if manifest.stat().st_nlink!=1 or manifest.stat().st_size>1024: raise Rejected('invalid_backup_manifest')
    expected=json.loads(manifest.read_text(encoding='utf-8'))
    if inspect(Config(data_dir=source,uid=config.uid,gid=config.gid))!=expected: raise Rejected('backup_hash_mismatch')
    old=inspect(config); old_id=identity(config.database)
    target=config.data_dir/'restore.tmp'
    with target.open('xb') as stream:
        os.chmod(target,0o600)
        with (source/'support.sqlite3').open('rb') as reader: copy_bounded(reader,stream)
        stream.flush(); os.fsync(stream.fileno())
    if sha(target)!=expected['sha256'] or inspect(config)!=old or identity(config.database)!=old_id: raise Rejected('restore_race')
    os.replace(target,config.database)
    fd=os.open(config.data_dir,os.O_RDONLY)
    try: os.fsync(fd)
    finally: os.close(fd)
    return inspect(config)

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation',choices=['check','backup','restore'])
    parser.add_argument('--directory',type=Path,default=Path('/var/lib/rag'))
    parser.add_argument('--backup-directory',type=Path)
    parser.add_argument('--confirm')
    args=parser.parse_args(argv)
    config=Config(data_dir=args.directory)
    try:
        if args.operation=='check': result=inspect(config)
        elif args.backup_directory is None: raise Rejected('backup_directory_required')
        elif args.operation=='backup':
            if args.confirm!='WRITERS-STOPPED': raise Rejected('stopped_writers_confirmation_required')
            result=backup(config,args.backup_directory)
        else: result=restore(config,args.backup_directory,args.confirm)
        print(json.dumps({'status':'PASS',**result}))
    except (Rejected,OSError,ValueError,RuntimeError): parser.exit(2,'Local storage operation rejected; inspect stopped-service recovery instructions.\n')

if __name__=='__main__': main()
