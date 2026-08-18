"""
鉴权管理模块
负责用户登录验证、登录失败锁定、会话管理和超时退出
- 连续5次错误密码 → 锁定1分钟
- 15分钟无操作 → 自动退出
"""
import time
import secrets
import threading
from datetime import datetime
from config import ADMIN_PASSWORD, MAX_LOGIN_ATTEMPTS, LOCKOUT_DURATION, SESSION_TIMEOUT

# 线程锁，确保并发安全
_auth_lock = threading.Lock()

# 登录失败记录：IP -> {count: 失败次数, locked_until: 锁定截止时间戳}
_login_failures = {}

# 会话存储：token -> {user: 用户名, login_time: 登录时间, last_activity: 最后活动时间戳}
_sessions = {}


def check_login(ip: str, password: str):
    """
    检查登录请求
    输入：ip - 客户端IP，password - 提交的密码
    输出：(success: bool, message: str, token: str or None)
    逻辑：
      1. 检查IP是否被锁定（5次失败后锁定1分钟）
      2. 验证密码
      3. 成功则创建会话Token，清除失败记录
      4. 失败则累计失败次数，达到上限时锁定
    """
    with _auth_lock:
        now = time.time()

        # 检查是否处于锁定状态
        if ip in _login_failures:
            record = _login_failures[ip]
            locked_until = record.get('locked_until', 0)
            if locked_until > now:
                remaining = int(locked_until - now)
                return False, f"登录失败次数过多，请 {remaining} 秒后重试", None

        # 验证密码
        if password == ADMIN_PASSWORD:
            # 登录成功，清除该IP的失败记录
            _login_failures.pop(ip, None)
            # 创建会话Token
            token = _generate_token()
            _sessions[token] = {
                'user': 'admin',
                'login_time': datetime.now().isoformat(),
                'last_activity': now
            }
            return True, "登录成功", token
        else:
            # 密码错误，累计失败次数
            if ip not in _login_failures:
                _login_failures[ip] = {'count': 0, 'locked_until': 0}

            _login_failures[ip]['count'] += 1
            fail_count = _login_failures[ip]['count']

            # 达到最大失败次数，锁定
            if fail_count >= MAX_LOGIN_ATTEMPTS:
                _login_failures[ip]['locked_until'] = now + LOCKOUT_DURATION
                _login_failures[ip]['count'] = 0  # 重置计数，锁定期间不累计
                return False, f"密码错误次数过多，已锁定 {LOCKOUT_DURATION} 秒", None

            remaining = MAX_LOGIN_ATTEMPTS - fail_count
            return False, f"密码错误，还剩 {remaining} 次尝试机会", None


def verify_session(token: str) -> bool:
    """
    验证会话Token是否有效
    输入：token - 会话Token
    输出：True=有效，False=无效或已超时
    逻辑：
      1. Token不存在 → 无效
      2. 超过15分钟无操作 → 删除会话，返回无效
      3. 有效则更新最后活动时间
    """
    with _auth_lock:
        if not token or token not in _sessions:
            return False

        session = _sessions[token]
        now = time.time()

        # 检查是否超时（15分钟无操作）
        if now - session['last_activity'] > SESSION_TIMEOUT:
            _sessions.pop(token, None)
            return False

        # 更新最后活动时间
        session['last_activity'] = now
        return True


def touch_session(token: str):
    """
    更新会话最后活动时间（延长超时）
    输入：token - 会话Token
    """
    with _auth_lock:
        if token in _sessions:
            _sessions[token]['last_activity'] = time.time()


def logout(token: str):
    """
    注销会话
    输入：token - 要注销的会话Token
    """
    with _auth_lock:
        _sessions.pop(token, None)


def get_user(token: str) -> str:
    """
    获取Token对应的用户名
    输入：token - 会话Token
    输出：用户名，无效Token返回None
    """
    with _auth_lock:
        if token in _sessions:
            return _sessions[token]['user']
        return None


def get_session_info(token: str) -> dict:
    """
    获取会话信息
    输入：token - 会话Token
    输出：会话信息字典，无效Token返回None
    """
    with _auth_lock:
        if token in _sessions:
            return dict(_sessions[token])
        return None


def _generate_token() -> str:
    """
    生成安全的会话Token
    输出：URL安全的随机Token字符串
    """
    return secrets.token_urlsafe(32)
