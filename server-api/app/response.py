"""统一响应封装：{ code, message, data }，与前端 api/http.ts 的解包约定一致。"""
from fastapi.responses import JSONResponse


def ok(data=None, message: str = "ok") -> JSONResponse:
    return JSONResponse(status_code=200, content={"code": 0, "message": message, "data": data})