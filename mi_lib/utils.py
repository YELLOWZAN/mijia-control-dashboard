"""
米家API工具函数模块
提供签名、哈希、随机数生成等加密工具函数
所有函数均为纯函数，无副作用
"""
import random
import string
import os
import time
import base64
import hashlib
import hmac
import json
from datetime import datetime
import colorama


def get_random(length: int) -> str:
    """
    生成指定长度的随机字符串（字母+数字）
    输入：length - 字符串长度
    输出：随机字符串
    """
    return ''.join(random.sample(string.ascii_letters + string.digits, length))


def generate_nonce() -> str:
    """
    生成请求nonce（8字节随机数 + 时间戳，Base64编码）
    输出：Base64编码的nonce字符串
    """
    nonce = os.urandom(8) + int(time.time() / 60).to_bytes(4, 'big')
    return base64.b64encode(nonce).decode()


def generate_signed_nonce(secret: str, nonce: str) -> str:
    """
    生成签名nonce（SHA256哈希）
    输入：secret - 密钥（Base64），nonce - 随机数（Base64）
    输出：Base64编码的签名nonce
    """
    m = hashlib.sha256()
    m.update(base64.b64decode(secret))
    m.update(base64.b64decode(nonce))
    return base64.b64encode(m.digest()).decode()


def generate_signature(url: str, signed_nonce: str, nonce: str, data: str) -> str:
    """
    生成请求签名（HMAC-SHA256）
    输入：url - 请求路径，signed_nonce - 签名nonce，nonce - 随机数，data - 请求数据
    输出：Base64编码的签名
    """
    sign = '&'.join([url, signed_nonce, nonce, 'data=' + data])
    signature = hmac.new(key=base64.b64decode(signed_nonce),
                         msg=sign.encode(),
                         digestmod=hashlib.sha256).digest()
    return base64.b64encode(signature).decode()


def sign_data(uri: str, data, secret: str) -> dict:
    """
    为请求数据签名（组装最终请求参数）
    输入：uri - 接口路径，data - 请求数据（dict或str），secret - 密钥
    输出：包含 _nonce, data, signature 的字典
    """
    if not isinstance(data, str):
        data = json.dumps(data)
    nonce = generate_nonce()
    signed_nonce = generate_signed_nonce(secret, nonce)
    signature = generate_signature(uri, signed_nonce, nonce, data)
    return {'_nonce': nonce, 'data': data, 'signature': signature}


def get_hash(password: str) -> str:
    """
    计算密码的MD5哈希（小米登录协议要求）
    输入：password - 明文密码
    输出：大写MD5哈希字符串
    """
    return hashlib.md5(password.encode()).hexdigest().upper()


def info_log(message: str, module: str = "提示", *args, **kwargs):
    """
    输出绿色提示日志
    输入：message - 日志内容，module - 模块名
    """
    s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sf = colorama.Fore.GREEN + message + colorama.Fore.RESET
    print(f"{s} [{module}] {sf}", *args, **kwargs)


def error_log(message: str, module: str = "错误", *args, **kwargs):
    """
    输出红色错误日志
    输入：message - 日志内容，module - 模块名
    """
    s = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sf = colorama.Fore.RED + message + colorama.Fore.RESET
    print(f"{s} [{module}] {sf}", *args, **kwargs)
