"""
Flask 子应用模块
提供米家设备控制 REST API，通过 WSGI 中间件挂载到 FastAPI
设备控制接口需鉴权（Authorization: Bearer <token>）
二维码登录接口无需鉴权（用于首次登录）
"""
import time
import threading
from flask import Flask, request, jsonify, Response
from auth_manager import verify_session, get_user
import database
import config

# 创建 Flask 应用实例
flask_app = Flask(__name__)

# MiService 延迟初始化（线程安全）
_mi_service = None
_mi_service_lock = threading.Lock()
_mi_service_error = None
_mi_verify_url = None

# 二维码登录缓存：ticket -> {qr_login, poll_url, created_at}
_qr_login_cache = {}
_qr_cache_lock = threading.Lock()


def get_verify_url():
    """获取二次验证URL（如果需要验证）"""
    return _mi_verify_url


def get_mi_service():
    """
    延迟获取 MiService 实例
    首次调用时初始化，后续返回缓存实例
    支持两种登录方式：
    1. 账号密码登录（.env 中配置 MI_USERNAME 和 MI_PASSWORD）
    2. 二维码扫码登录（无需账号密码，使用已保存的 token）
    输出：(MiService实例, 错误信息) 元组
    """
    global _mi_service, _mi_service_error, _mi_verify_url
    if _mi_service is not None:
        return _mi_service, None

    with _mi_service_lock:
        # 双重检查，避免重复初始化
        if _mi_service is not None:
            return _mi_service, None

        # 检查是否有已保存的 token（二维码登录场景）
        from mi_lib.account import TokenStore
        token_store = TokenStore()
        has_token = bool(token_store.token and token_store.token.get("service_token"))

        # 如果没有 token 且没有配置账号密码，提示需要登录
        if not has_token and (not config.MI_USERNAME or not config.MI_PASSWORD):
            _mi_service_error = "小米账号未登录，请使用扫码登录或在 .env 文件中配置账号密码"
            return None, _mi_service_error

        try:
            from mi_lib.service import MiService
            from mi_lib.account import NeedVerifyError
            # 账号密码可为空（二维码登录模式），MiAccountSession 会优先使用已保存的 token
            _mi_service = MiService(
                config.MI_USERNAME or "",
                config.MI_PASSWORD or "",
                config.MI_NICKNAME
            )
            _mi_service_error = None
            _mi_verify_url = None
            return _mi_service, None
        except NeedVerifyError as e:
            _mi_service_error = str(e)
            _mi_verify_url = e.verify_url
            return None, _mi_service_error
        except Exception as e:
            _mi_service_error = f"小米账号登录失败: {str(e)}"
            _mi_verify_url = None
            return None, _mi_service_error


def reset_mi_service():
    """
    重置 MiService 实例（用于配置变更后重新初始化）
    """
    global _mi_service, _mi_service_error, _mi_verify_url
    with _mi_service_lock:
        _mi_service = None
        _mi_service_error = None
        _mi_verify_url = None


@flask_app.before_request
def check_auth():
    """
    请求前鉴权检查
    验证 Authorization 头中的 Token 是否有效
    无效则返回 401
    二维码登录相关接口（/miapi/qr/*）无需鉴权
    """
    if request.method == 'OPTIONS':
        return None

    # 二维码登录接口无需鉴权（用于首次登录小米账号）
    if request.path.startswith('/qr/'):
        return None

    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    if not verify_session(token):
        return jsonify({"code": 401, "message": "未授权或会话已过期"}), 401


def _get_current_user() -> str:
    """从请求头获取当前登录用户名"""
    token = request.headers.get('Authorization', '').replace('Bearer ', '')
    return get_user(token) or "unknown"


def _handle_form():
    """从请求中获取数据（支持JSON和表单格式）"""
    data = request.get_json(silent=True)
    if data is None:
        data = request.form.to_dict()
    return data


@flask_app.route('/get_device_list', methods=['GET'])
def get_device_list():
    """
    获取设备列表
    输出：{code: 0, msg: "success", data: [设备列表]}
    """
    service, err = get_mi_service()
    if err:
        return jsonify({"code": 1, "msg": err, "data": []})

    result = service.get_device_list()
    return jsonify(result)


@flask_app.route('/do_action', methods=['POST'])
def do_action():
    """
    执行设备动作
    输入：device_id - 设备ID，device_name - 设备名称（二选一），
          sid - 服务ID，aid - 动作ID，action - 参数列表
    输出：{code: 0, msg: "success", data: {...}}
    """
    data = _handle_form()
    device_id = data.get('device_id')
    device_name = data.get('device_name')

    service, err = get_mi_service()
    if err:
        return jsonify({"code": 1, "msg": err, "data": {}})

    # 获取设备实例
    try:
        if device_id:
            device = service.use_device(device_id)
        elif device_name:
            device = service.find_device(device_name)
        else:
            return jsonify({"code": 400, "msg": "缺少 device_id 或 device_name", "data": {}})
    except Exception as e:
        return jsonify({"code": 404, "msg": f"设备未找到: {str(e)}", "data": {}})

    sid = data.get('sid')
    aid = data.get('aid')
    action = data.get('action', [])

    # 执行动作
    result = device.do_action(sid, aid, action)

    # 记录操作日志
    database.log_operation(
        user=_get_current_user(),
        action="do_action",
        device_id=str(device_id),
        device_name=str(device_name),
        params=str({"sid": sid, "aid": aid, "action": action}),
        result=str(result),
        success=result.get("code") == 0
    )

    return jsonify(result)


@flask_app.route('/get_prop', methods=['POST'])
def get_prop():
    """
    获取单个设备属性
    输入：device_id/device_name, sid, pid
    输出：{code: 0, msg: "success", data: {value: ...}}
    """
    data = _handle_form()
    device_id = data.get('device_id')
    device_name = data.get('device_name')

    service, err = get_mi_service()
    if err:
        return jsonify({"code": 1, "msg": err, "data": {}})

    try:
        if device_id:
            device = service.use_device(device_id)
        elif device_name:
            device = service.find_device(device_name)
        else:
            return jsonify({"code": 400, "msg": "缺少 device_id 或 device_name", "data": {}})
    except Exception as e:
        return jsonify({"code": 404, "msg": f"设备未找到: {str(e)}", "data": {}})

    sid = data.get('sid')
    pid = data.get('pid')
    result = device.get_device_prop(sid, pid)

    database.log_operation(
        user=_get_current_user(),
        action="get_prop",
        device_id=str(device_id),
        device_name=str(device_name),
        params=str({"sid": sid, "pid": pid}),
        result=str(result),
        success=result.get("code") == 0
    )

    return jsonify(result)


@flask_app.route('/get_props', methods=['POST'])
def get_props():
    """
    批量获取设备属性
    输入：device_id/device_name, params - [{"siid": sid, "piid": pid}, ...]
    输出：{code: 0, msg: "success", data: [...]}
    """
    data = _handle_form()
    device_id = data.get('device_id')
    device_name = data.get('device_name')

    service, err = get_mi_service()
    if err:
        return jsonify({"code": 1, "msg": err, "data": []})

    try:
        if device_id:
            device = service.use_device(device_id)
        elif device_name:
            device = service.find_device(device_name)
        else:
            return jsonify({"code": 400, "msg": "缺少 device_id 或 device_name", "data": []})
    except Exception as e:
        return jsonify({"code": 404, "msg": f"设备未找到: {str(e)}", "data": []})

    params = data.get('params', [])
    result = device.get_device_props(params)

    database.log_operation(
        user=_get_current_user(),
        action="get_props",
        device_id=str(device_id),
        device_name=str(device_name),
        params=str(params),
        result=str(result),
        success=result.get("code") == 0
    )

    return jsonify(result)


@flask_app.route('/set_prop', methods=['POST'])
def set_prop():
    """
    设置单个设备属性
    输入：device_id/device_name, sid, pid, value
    输出：{code: 0, msg: "success", data: {...}}
    """
    data = _handle_form()
    device_id = data.get('device_id')
    device_name = data.get('device_name')

    service, err = get_mi_service()
    if err:
        return jsonify({"code": 1, "msg": err, "data": {}})

    try:
        if device_id:
            device = service.use_device(device_id)
        elif device_name:
            device = service.find_device(device_name)
        else:
            return jsonify({"code": 400, "msg": "缺少 device_id 或 device_name", "data": {}})
    except Exception as e:
        return jsonify({"code": 404, "msg": f"设备未找到: {str(e)}", "data": {}})

    sid = data.get('sid')
    pid = data.get('pid')
    value = data.get('value')
    result = device.set_device_prop(sid, pid, value)

    database.log_operation(
        user=_get_current_user(),
        action="set_prop",
        device_id=str(device_id),
        device_name=str(device_name),
        params=str({"sid": sid, "pid": pid, "value": value}),
        result=str(result),
        success=result.get("code") == 0
    )

    return jsonify(result)


@flask_app.route('/set_props', methods=['POST'])
def set_props():
    """
    批量设置设备属性
    输入：device_id/device_name, params - [{"siid": sid, "piid": pid, "value": value}, ...]
    输出：{code: 0, msg: "success", data: [...]}
    """
    data = _handle_form()
    device_id = data.get('device_id')
    device_name = data.get('device_name')

    service, err = get_mi_service()
    if err:
        return jsonify({"code": 1, "msg": err, "data": []})

    try:
        if device_id:
            device = service.use_device(device_id)
        elif device_name:
            device = service.find_device(device_name)
        else:
            return jsonify({"code": 400, "msg": "缺少 device_id 或 device_name", "data": []})
    except Exception as e:
        return jsonify({"code": 404, "msg": f"设备未找到: {str(e)}", "data": []})

    params = data.get('params', [])
    result = device.set_device_props(params)

    database.log_operation(
        user=_get_current_user(),
        action="set_props",
        device_id=str(device_id),
        device_name=str(device_name),
        params=str(params),
        result=str(result),
        success=result.get("code") == 0
    )

    return jsonify(result)


@flask_app.route('/get_spec', methods=['POST'])
def get_spec():
    """
    获取设备规格定义（spec）
    根据设备model从 home.miot-spec.com 获取属性规格，用于动态生成控制UI
    输入：{model: "设备型号"}
    输出：{code: 0, data: {urn, model, services: [...]}}
    """
    data = request.get_json(silent=True) or {}
    model = data.get('model', '')

    if not model:
        return jsonify({"code": 1, "message": "缺少model参数"})

    try:
        from mi_lib.spec import get_device_spec, build_control_items
        spec = get_device_spec(model)
        control_items = build_control_items(spec)

        return jsonify({
            "code": 0,
            "data": {
                "urn": spec.get("urn", ""),
                "model": model,
                "services": spec.get("services", []),
                "control_items": control_items,
            }
        })
    except Exception as e:
        return jsonify({"code": 1, "message": f"获取设备规格失败: {str(e)}"})


# ============================================================
# 二维码登录接口（无需鉴权，用于首次登录小米账号）
# ============================================================

@flask_app.route('/qr/generate', methods=['POST'])
def qr_generate():
    """
    生成小米账号登录二维码
    输出：{code: 0, data: {ticket, qr_image_url}}
    """
    from mi_lib.qr_login import MiQrLogin
    from mi_lib import utils

    try:
        # 使用持久化的 device_id 或生成新的
        device_id = utils.get_random(16)
        qr_login = MiQrLogin(device_id=device_id)
        qr_info = qr_login.generate_qr()

        if not qr_info.get('qr_image_url'):
            return jsonify({"code": 1, "message": "生成二维码失败，未获取到二维码图片URL"})

        # 使用 ticket 作为缓存标识，如果没有 ticket 则用时间戳
        ticket = qr_info.get('ticket') or f"tk_{int(time.time()*1000)}"

        # 缓存 MiQrLogin 实例，用于后续轮询和图片代理
        with _qr_cache_lock:
            # 清理过期缓存（5分钟前的）
            expired_keys = [k for k, v in _qr_login_cache.items()
                           if time.time() - v['created_at'] > 300]
            for k in expired_keys:
                _qr_login_cache.pop(k, None)

            _qr_login_cache[ticket] = {
                'qr_login': qr_login,
                'poll_url': qr_info['poll_url'],
                'qr_image_url': qr_info['qr_image_url'],
                'created_at': time.time(),
                'device_id': device_id,
            }

        return jsonify({
            "code": 0,
            "data": {
                "ticket": ticket,
                "qr_image_url": f"/qr/image?ticket={ticket}",
            }
        })
    except Exception as e:
        return jsonify({"code": 1, "message": f"生成二维码失败: {str(e)}"})


@flask_app.route('/qr/image', methods=['GET'])
def qr_image():
    """
    代理获取二维码图片（避免浏览器跨域问题）
    输入：ticket - 二维码ticket
    输出：PNG 图片字节流
    """
    ticket = request.args.get('ticket', '')
    if not ticket:
        return "Missing ticket", 400

    with _qr_cache_lock:
        cache_item = _qr_login_cache.get(ticket)

    if not cache_item:
        return "二维码已过期，请重新生成", 404

    qr_login = cache_item['qr_login']
    qr_image_url = cache_item['qr_image_url']
    try:
        image_bytes = qr_login.fetch_qr_image(qr_image_url)
        return Response(image_bytes, mimetype='image/png')
    except Exception as e:
        return f"获取二维码图片失败: {str(e)}", 500


@flask_app.route('/qr/poll', methods=['POST'])
def qr_poll():
    """
    轮询二维码扫码状态
    输入：{ticket: "xxx"}
    输出：{code: 0, data: {status, message}}
         status: waiting/scanned/confirmed/expired/canceled/failed
    """
    from mi_lib.qr_login import QR_STATUS_CONFIRMED

    data = request.get_json(silent=True) or {}
    ticket = data.get('ticket', '')

    if not ticket:
        return jsonify({"code": 1, "message": "缺少ticket参数"})

    with _qr_cache_lock:
        cache_item = _qr_login_cache.get(ticket)

    if not cache_item:
        return jsonify({
            "code": 0,
            "data": {"status": "expired", "message": "二维码已过期，请重新生成"}
        })

    # 检查缓存是否过期（5分钟）
    if time.time() - cache_item['created_at'] > 300:
        with _qr_cache_lock:
            _qr_login_cache.pop(ticket, None)
        return jsonify({
            "code": 0,
            "data": {"status": "expired", "message": "二维码已过期，请重新生成"}
        })

    qr_login = cache_item['qr_login']
    poll_url = cache_item['poll_url']

    try:
        result = qr_login.poll_once(poll_url)

        # 登录成功，保存 token
        if result['status'] == QR_STATUS_CONFIRMED and result.get('token'):
            token = result['token']
            from mi_lib.account import TokenStore
            token_store = TokenStore()
            token_store.save_token(token)

            # 重置 MiService，使其使用新 token
            reset_mi_service()

            # 清理缓存
            with _qr_cache_lock:
                _qr_login_cache.pop(ticket, None)

            database.log_connection(
                user="qr_login",
                success=True,
                message=f"二维码登录成功，用户ID: {token.get('user_id')}"
            )
        else:
            database.log_connection(
                user="qr_login",
                success=False,
                message=result.get('message', '')
            )

        return jsonify({
            "code": 0,
            "data": {
                "status": result['status'],
                "message": result['message']
            }
        })
    except Exception as e:
        return jsonify({
            "code": 1,
            "message": f"轮询失败: {str(e)}"
        })
