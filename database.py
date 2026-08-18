"""
数据库管理模块
使用 SQLite 存储操作日志、连接日志和持久化数据
日志保留两周，到期自动清理
"""
import sqlite3
import threading
from datetime import datetime, timedelta
from config import DATABASE_PATH

# 线程锁，确保数据库操作的线程安全
_db_lock = threading.Lock()


def get_db() -> sqlite3.Connection:
    """
    获取数据库连接
    输出：sqlite3.Connection 实例（Row工厂模式，支持按列名访问）
    """
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """
    初始化数据库
    创建操作日志、连接日志、设置表
    在应用启动时调用
    """
    with _db_lock:
        conn = get_db()
        c = conn.cursor()

        # 操作日志表 - 记录设备操作（获取属性、设置属性、执行动作等）
        c.execute('''
            CREATE TABLE IF NOT EXISTS operation_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                user TEXT NOT NULL,
                action TEXT NOT NULL,
                device_id TEXT,
                device_name TEXT,
                params TEXT,
                result TEXT,
                success INTEGER DEFAULT 1
            )
        ''')

        # 连接日志表 - 记录登录、登出、超时等连接事件
        c.execute('''
            CREATE TABLE IF NOT EXISTS connection_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ip TEXT,
                user_agent TEXT,
                action TEXT NOT NULL,
                username TEXT,
                success INTEGER DEFAULT 1
            )
        ''')

        # 设置表 - 持久化存储其他数据（用户偏好、配置等）
        c.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT
            )
        ''')

        conn.commit()
        conn.close()


def log_operation(user: str, action: str, device_id: str = None,
                  device_name: str = None, params: str = None,
                  result: str = None, success: bool = True):
    """
    记录设备操作日志
    输入：user - 操作用户，action - 操作类型，device_id - 设备ID，
          device_name - 设备名称，params - 操作参数，result - 操作结果，
          success - 是否成功
    """
    with _db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            INSERT INTO operation_logs
                (timestamp, user, action, device_id, device_name, params, result, success)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now().isoformat(), user, action, device_id, device_name,
            params, result, 1 if success else 0
        ))
        conn.commit()
        conn.close()


def log_connection(ip: str, user_agent: str, action: str,
                   username: str, success: bool = True):
    """
    记录连接日志（登录、登出、超时等）
    输入：ip - 客户端IP，user_agent - 浏览器标识，action - 动作类型，
          username - 用户名，success - 是否成功
    """
    with _db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            INSERT INTO connection_logs
                (timestamp, ip, user_agent, action, username, success)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            datetime.now().isoformat(), ip, user_agent, action,
            username, 1 if success else 0
        ))
        conn.commit()
        conn.close()


def cleanup_old_logs():
    """
    清理两周前的日志
    删除 operation_logs 和 connection_logs 中超过14天的记录
    在应用启动时和每天定时调用
    """
    with _db_lock:
        conn = get_db()
        c = conn.cursor()
        cutoff = (datetime.now() - timedelta(days=14)).isoformat()
        c.execute('DELETE FROM operation_logs WHERE timestamp < ?', (cutoff,))
        c.execute('DELETE FROM connection_logs WHERE timestamp < ?', (cutoff,))
        conn.commit()
        conn.close()


def get_operation_logs(limit: int = 100) -> list:
    """
    查询操作日志
    输入：limit - 返回条数上限
    输出：日志记录列表（按时间倒序）
    """
    with _db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            'SELECT * FROM operation_logs ORDER BY timestamp DESC LIMIT ?',
            (limit,)
        )
        rows = c.fetchall()
        conn.close()
        return [dict(row) for row in rows]


def get_connection_logs(limit: int = 100) -> list:
    """
    查询连接日志
    输入：limit - 返回条数上限
    输出：日志记录列表（按时间倒序）
    """
    with _db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute(
            'SELECT * FROM connection_logs ORDER BY timestamp DESC LIMIT ?',
            (limit,)
        )
        rows = c.fetchall()
        conn.close()
        return [dict(row) for row in rows]


def set_setting(key: str, value: str):
    """
    保存设置项（持久化存储）
    输入：key - 键名，value - 值
    """
    with _db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO settings (key, value, updated_at)
            VALUES (?, ?, ?)
        ''', (key, value, datetime.now().isoformat()))
        conn.commit()
        conn.close()


def get_setting(key: str, default: str = None) -> str:
    """
    读取设置项
    输入：key - 键名，default - 默认值
    输出：对应的值，不存在时返回default
    """
    with _db_lock:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT value FROM settings WHERE key = ?', (key,))
        row = c.fetchone()
        conn.close()
        return row['value'] if row else default
