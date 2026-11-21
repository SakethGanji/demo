"""Drive the Vela agent's batch_score tool (agent/tools.py, _dataset_csv) against the running API, as the agent loop does."""
import asyncio, sys, time
import httpx
sys.path.insert(0, "backend")  # run from the repo root, with ENSEMBLE_DATA_DIR = the API's data dir
from app.agent import tools as T

async def main(version_id, dataset_id):
    async with httpx.AsyncClient(base_url="http://localhost:8000", headers={"X-Ensemble-Actor": "Vela agent on behalf of Ana"}) as c:
        ctx = T.Ctx(client=c, app=None, actor="Vela agent on behalf of Ana", deadline=time.monotonic() + 900)
        tool = next(t for t in T.TOOLS if t.name == "batch_score")
        out = await tool.run(ctx, {"version_id": version_id, "dataset_id": dataset_id, "limit": 50})
        print({k: out.get(k) for k in list(out)[:10]})
        return out

asyncio.run(main(sys.argv[1], sys.argv[2]))
