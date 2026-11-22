import asyncio, os, sys, time, faulthandler
sys.path.insert(0, "/home/user/demo/backend"); sys.path.insert(0, "/var/tmp/fx5/pooltest")
from app.engine.pool import HeavyPool
from concurrent.futures.process import BrokenProcessPool
import pt_helpers as H
def _main():
    faulthandler.dump_traceback_later(50)
    p = HeavyPool(workers=2)
    print("ok", p.call(H.ok))
    t = time.time()
    try:
        p.call(H.die)
    except BrokenProcessPool as e:
        print("call die ->", type(e).__name__, e, round(time.time()-t, 1), "s")
    print("next call ok", p.call(H.ok))
    async def main():
        t = time.time()
        try:
            await p.run(H.die)
        except BrokenProcessPool as e:
            print("run die ->", e, round(time.time()-t, 1), "s")
        print("next run ok", await p.run(H.ok))
    asyncio.run(main())
    p.shutdown()
    
if __name__ == "__main__":
    _main()
