from studio import api, HTTPError
import studio

items = []


def create_item(req):
    return {"ok": True}


@api.get("/broken", name="getBroken")
def get_broken(req):
    return {"ok": True}
