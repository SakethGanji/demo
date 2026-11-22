"""F-X5a: the X5 K2 hang. Two calls on one heavy pool; one pool process is SIGKILLed (as by the OOM killer) while the
other call waits. Before: the second call never returns. After: both fail at once, and the next call works."""
import asyncio, os, sys, time, faulthandler
sys.path.insert(0, "/home/user/demo/backend"); sys.path.insert(0, "/var/tmp/fx5/pooltest")
from app.engine.pool import HeavyPool
import pt_helpers as H


def _main():
    p = HeavyPool(workers=2)
    p.call(H.ok) if hasattr(p, "call") else p.submit(H.ok).result()

    async def main():
        t = time.time()
        async def one(name, fn, *a):
            try:
                r = await asyncio.wait_for(p.run(fn, *a), 40)
                return f"{name}: {r}"
            except asyncio.TimeoutError:
                return f"{name}: NO ANSWER after 40 s"
            except Exception as e:  # noqa
                return f"{name}: {type(e).__name__} after {time.time()-t:.1f} s"
        res = await asyncio.gather(one("waiting call (slow 8 s)", H.slow, 8), (asyncio.sleep(0.5) and None) or one("killed call", H.die))
        for r in res:
            print(" ", r)
        print("  next call:", await asyncio.wait_for(p.run(H.ok), 30), f"({time.time()-t:.1f} s)")
    asyncio.run(main())
    os._exit(0)


if __name__ == "__main__":
    _main()
