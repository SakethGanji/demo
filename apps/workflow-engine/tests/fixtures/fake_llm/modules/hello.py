from studio import api


@api.get("/hello", name="hello")
def hello(req):
    return {"hello": "world"}
