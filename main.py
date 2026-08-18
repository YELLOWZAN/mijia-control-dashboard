"""
FastAPI 主应用入口
提供 Web 界面、鉴权接口、日志查看，并挂载 Flask 子应用提供设备控制 API
启动方式：python main.py
访问地址：http://localhost:8000
"""
import threading
import time
import os
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.wsgi import WSGIMiddleware

import config
import database
import auth_manager
from flask_app import flask_app, get_mi_service, reset_mi_service, get_verify_url

# 创建 FastAPI 应用
app = FastAPI(title="米家设备控制台", docs_url=None, redoc_url=None)

# 挂载静态文件目录
app.mount("/static", StaticFiles(directory="static"), name="static")

# 挂载 Flask 子应用（设备控制 API 挂载在 /miapi 路径下）
app.mount("/miapi", WSGIMiddleware(flask_app))

# 模板引擎
templates = Jinja2Templates(directory="templates")


@app.on_event("startup")
def on_startup():
    """
    应用启动时执行：
    1. 初始化数据库
    2. 清理过期日志
    3. 启动日志定时清理后台线程
    """
    database.init_db()
    database.cleanup_old_logs()
    _start_log_cleanup_task()


def _start_log_cleanup_task():
    """
    启动后台线程，每24小时清理一次过期日志
    日志保留两周，超过14天的自动删除
    """
    def cleanup_loop():
        while True:
            time.sleep(24 * 60 * 60)  # 24小时
            try:
                database.cleanup_old_logs()
            except Exception:
                pass

    thread = threading.Thread(target=cleanup_loop, daemon=True)
    thread.start()


# ========== 页面路由 ==========

@app.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    """登录页面"""
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """控制台主页（前端JS会验证鉴权状态）"""
    return templates.TemplateResponse("index.html", {"request": request})


# ========== 鉴权 API ==========

@app.post("/api/auth/login")
async def login(request: Request):
    """
    用户登录接口
    输入：{password: "密码"}
    输出：{code: 0, message: "登录成功", token: "..."}
    逻辑：验证密码，5次失败锁定1分钟
    """
    try:
        body = await request.json()
    except Exception:
        body = {}

    password = body.get("password", "")
    ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")

    success, message, token = auth_manager.check_login(ip, password)

    # 记录连接日志
    database.log_connection(ip, user_agent, "login", "admin", success)

    if success:
        return {"code": 0, "message": message, "token": token}
    else:
        return {"code": 1, "message": message}


@app.post("/api/auth/logout")
async def logout(request: Request):
    """
    用户登出接口
    输入：Authorization 头携带 Token
    输出：{code: 0, message: "已退出登录"}
    """
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "")

    auth_manager.logout(token)
    database.log_connection(ip, user_agent, "logout", "admin", True)

    return {"code": 0, "message": "已退出登录"}


@app.get("/api/auth/check")
async def check_auth(request: Request):
    """
    检查会话状态
    输入：Authorization 头携带 Token
    输出：{code: 0, valid: true, user: "admin"}
    """
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    valid = auth_manager.verify_session(token)

    if valid:
        user = auth_manager.get_user(token)
        return {"code": 0, "valid": True, "user": user}
    else:
        return {"code": 401, "valid": False, "message": "会话无效或已过期"}


@app.post("/api/auth/touch")
async def touch_session(request: Request):
    """
    刷新会话活动时间（用于前端定时保活）
    输入：Authorization 头携带 Token
    输出：{code: 0, message: "ok"}
    """
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    auth_manager.touch_session(token)
    return {"code": 0, "message": "ok"}


# ========== 日志 API ==========

@app.get("/api/settings")
async def get_settings(request: Request):
    """
    读取系统设置（颜色模式/透明度/卡片数量限制等）
    输出：{code: 0, data: {colorMode, cardOpacity, sidebarOpacity, headerOpacity, bgBlur, bgOverlay, pageLimit}}
    说明：背景图片以 dataURL 形式存浏览器 localStorage，不上传服务器
    """
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not auth_manager.verify_session(token):
        return JSONResponse({"code": 401, "message": "未授权"}, status_code=401)

    defaults = {
        "colorMode": "light",
        "cardOpacity": "100",
        "sidebarOpacity": "100",
        "headerOpacity": "100",
        "bgBlur": "0",
        "bgOverlay": "0",
        "pageLimit": "12",
    }
    data = {k: database.get_setting(k, v) for k, v in defaults.items()}
    return {"code": 0, "data": data}


@app.post("/api/settings")
async def save_settings(request: Request):
    """
    保存系统设置（逐项持久化）
    输入：JSON，仅包含需要更新的字段
    输出：{code: 0, message: "已保存"}
    """
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not auth_manager.verify_session(token):
        return JSONResponse({"code": 401, "message": "未授权"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        body = {}

    allowed = {
        "colorMode", "cardOpacity", "sidebarOpacity",
        "headerOpacity", "bgBlur", "bgOverlay", "pageLimit",
    }
    saved = 0
    for k, v in body.items():
        if k in allowed:
            # 数值字段做范围校验，防越界
            if k in ("cardOpacity", "sidebarOpacity", "headerOpacity"):
                v = str(max(30, min(100, int(v))))
            elif k == "bgBlur":
                v = str(max(0, min(20, int(v))))
            elif k == "bgOverlay":
                v = str(max(0, min(80, int(v))))
            elif k == "pageLimit":
                v = str(max(4, min(48, int(v))))
            elif k == "colorMode":
                v = "dark" if v == "dark" else "light"
            database.set_setting(k, str(v))
            saved += 1

    return {"code": 0, "message": f"已保存 {saved} 项设置"}


@app.get("/api/logs/operation")
async def get_operation_logs(request: Request, limit: int = 100):
    """
    查询操作日志
    输入：limit - 返回条数（最大200）
    输出：{code: 0, data: [日志列表]}
    """
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not auth_manager.verify_session(token):
        return JSONResponse({"code": 401, "message": "未授权"}, status_code=401)

    limit = min(limit, 200)
    logs = database.get_operation_logs(limit)
    return {"code": 0, "data": logs}


@app.get("/api/logs/connection")
async def get_connection_logs(request: Request, limit: int = 100):
    """
    查询连接日志
    输入：limit - 返回条数（最大200）
    输出：{code: 0, data: [日志列表]}
    """
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not auth_manager.verify_session(token):
        return JSONResponse({"code": 401, "message": "未授权"}, status_code=401)

    limit = min(limit, 200)
    logs = database.get_connection_logs(limit)
    return {"code": 0, "data": logs}


# ========== 服务状态 API ==========

@app.get("/api/status")
async def get_status(request: Request):
    """
    获取服务状态（小米账号是否已连接）
    输出：{code: 0, connected: bool, message: str, nickname: str}
    """
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not auth_manager.verify_session(token):
        return JSONResponse({"code": 401, "message": "未授权"}, status_code=401)

    service, err = get_mi_service()
    if service:
        account = service.get_account_info()
        return {
            "code": 0,
            "connected": True,
            "message": "已连接",
            "nickname": account.get("nickname", "")
        }
    else:
        # 检查是否需要二次验证
        need_verify = err and ("新设备验证" in err or "二次验证" in err)
        verify_url = get_verify_url() if need_verify else None
        return {
            "code": 0,
            "connected": False,
            "message": err or "未连接",
            "need_verify": need_verify,
            "verify_url": verify_url
        }


@app.post("/api/retry-connect")
async def retry_connect(request: Request):
    """
    重试连接小米账号
    用户在浏览器完成小米账号验证后，点击重试按钮调用此接口
    输出：{code: 0, connected: bool, message: str, verify_url: str}
    """
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not auth_manager.verify_session(token):
        return JSONResponse({"code": 401, "message": "未授权"}, status_code=401)

    # 重置 MiService，强制重新登录（复用已保存的 device_id）
    reset_mi_service()

    # 重新获取服务实例
    service, err = get_mi_service()
    if service:
        return {"code": 0, "connected": True, "message": "连接成功"}
    else:
        need_verify = err and ("新设备验证" in err or "二次验证" in err)
        verify_url = get_verify_url() if need_verify else None
        return {
            "code": 0,
            "connected": False,
            "message": err or "未连接",
            "need_verify": need_verify,
            "verify_url": verify_url
        }


# ========== 启动入口 ==========

if __name__ == "__main__":
    import uvicorn
    print(f"米家设备控制台启动中...")
    print(f"访问地址: http://localhost:{config.PORT}")
    uvicorn.run(app, host=config.HOST, port=config.PORT)
