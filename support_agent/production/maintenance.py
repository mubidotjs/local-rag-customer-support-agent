"""Network-disabled Compose CLI role; fixed index, private creation permissions."""
import os
import sys
from .config import Config,Rejected
from .storage import directory
from ..cli import main as documents

def main(argv=None):
    args=list(sys.argv[1:] if argv is None else argv)
    try:
        if not args or args[0] not in {'status','recover','preview','apply'} or any(a.startswith('--allow') for a in args):
            raise Rejected('offline_maintenance_command_required')
        c=Config.environment()
        if c.mode!='disabled' or os.name!='posix' or os.getuid()!=10001 or os.getgid()!=10001:
            raise Rejected('offline_identity_required')
        directory(c.data_dir,10001,10001)
        os.umask(0o077)
        return documents(['documents','--db',str(c.database),*args])
    except (Rejected,OSError,ValueError):
        raise SystemExit('Offline maintenance configuration rejected.')

if __name__=='__main__': main()
