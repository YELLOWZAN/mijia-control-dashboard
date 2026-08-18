"""
配置加载模块
从 .env 文件加载所有配置项，提供全局访问
输入：.env 文件
输出：各配置常量
"""
import os
from dotenv import load_dotenv

# 加载 .env 文件中的环境变量
load_dotenv()

# ========== 小米账号配置 ==========
MI_USERNAME = os.getenv("MI_USERNAME", "")
MI_PASSWORD = os.getenv("MI_PASSWORD", "")
MI_NICKNAME = os.getenv("MI_NICKNAME", "")

# ========== 管理员配置 ==========
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

# ========== 应用配置 ==========
SECRET_KEY = os.getenv("SECRET_KEY", "default-secret-key-please-change")
DATABASE_PATH = os.getenv("DATABASE_PATH", "./mi_control.db")

# ========== 服务器配置 ==========
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

# ========== 鉴权配置 ==========
MAX_LOGIN_ATTEMPTS = 5          # 最大连续登录失败次数
LOCKOUT_DURATION = 60          # 锁定时长（秒）
SESSION_TIMEOUT = 15 * 60      # 会话超时（秒，15分钟无操作自动退出）
