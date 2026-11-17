import logging
import os
import sys
import uvicorn
from .config import Config,Rejected
from .app import Production,emit

def main():
    logging.basicConfig(level=logging.INFO,format='%(message)s',stream=sys.stdout)
    os.umask(0o077)
    try: app=Production(Config.environment())
    except (Rejected,ValueError,OSError):
        emit('startup_rejected',reason='invalid_local_configuration'); return 2
    emit('starting',mode=app.config.mode)
    uvicorn.run(app,host='127.0.0.1',port=8000,workers=1,limit_concurrency=16,backlog=32,
                timeout_keep_alive=5,timeout_graceful_shutdown=10,access_log=False,server_header=False,log_config=None)
    return 0

if __name__=='__main__': raise SystemExit(main())
