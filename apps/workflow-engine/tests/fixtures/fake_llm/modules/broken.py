from studio import api


@api.get("/broken", name="broken")
def broken(req):
    total = 0
    for i in range(3)
        total += i
    return {"total": total}
