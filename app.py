from fastapi import FastAPI, Request

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

@app.head("/health")
def health_head():
    return Response(status_code=200)

@app.get("/ip")
def ip(request: Request):
    return {
        "client_host": request.client.host,
        "x_real_ip": request.headers.get("x-real-ip"),
        "x_forwarded_for": request.headers.get("x-forwarded-for"),
        "x_forwarded_proto": request.headers.get("x-forwarded-proto"),
        "host": request.headers.get("host"),
    }
