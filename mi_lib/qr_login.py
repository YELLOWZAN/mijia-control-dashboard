"""
小米账号二维码登录模块
通过米家APP扫码完成登录，避免新设备验证问题
所有操作在程序 session 内完成，不依赖浏览器 Cookie

流程（基于小米 longPolling 接口）：
1. GET /longPolling/loginUrl 获取二维码图片URL和长轮询URL
2. 用户用米家APP扫码
3. 长轮询 lp URL 等待登录成功
4. 获取 location，用 SHA1(nonce+ssecurity) 生成 clientSign 换取 serviceToken
"""
import json
import time
import base64
import hashlib
import requests
from urllib import parse
from mi_lib import utils


# 二维码登录状态常量
QR_STATUS_WAITING = "waiting"       # 等待扫码
QR_STATUS_SCANNED = "scanned"       # 已扫码未确认
QR_STATUS_CONFIRMED = "confirmed"   # 已确认登录
QR_STATUS_EXPIRED = "expired"       # 二维码过期
QR_STATUS_CANCELED = "canceled"     # 用户取消
QR_STATUS_FAILED = "failed"         # 登录失败


class MiQrLogin:
    """
    小米账号二维码登录器
    负责生成二维码并轮询扫码状态，最终获取 serviceToken
    输入：device_id - 设备ID（建议持久化复用）
    """

    # 小米 Passport 基础 URL
    PASSPORT_BASE = "https://account.xiaomi.com"
    LOGIN_URL_ENDPOINT = f"{PASSPORT_BASE}/longPolling/loginUrl"

    # 标准 User-Agent，与米家 iOS 客户端一致
    USER_AGENT = 'APP/com.xiaomi.mihome APPV/6.0.103 iosPassportSDK/3.9.0 iOS/14.4 miHSTS'

    def __init__(self, device_id: str):
        """
        初始化二维码登录器
        输入：device_id - 16位设备ID
        """
        self.device_id = device_id
        self.session = requests.Session()
        self.headers = {
            'User-Agent': self.USER_AGENT,
            'x-xiaomi-protocal-flag-cli': 'PROTOCAL-HTTP2'
        }
        self.cookies = {'sdkVersion': '3.9', 'deviceId': device_id}

    def _parse_response(self, response: requests.Response) -> dict:
        """
        解析小米 Passport 响应
        小米接口响应体有以下前缀格式：
        - &&&START&&& （11字符）
        - )]}'\n （11字符）
        - )]}' （4字符）
        输入：response - requests 响应对象
        输出：解析后的 dict
        """
        text = response.text
        if text.startswith("&&&START&&&"):
            text = text[11:]
        elif text.startswith(")]}'"):
            text = text[11:] if text.startswith(")]}'\n") else text[4:]
        return json.loads(text)

    def generate_qr(self) -> dict:
        """
        生成二维码（对外接口）
        通过小米 longPolling/loginUrl 接口获取二维码和长轮询URL
        输出：{ticket, qr_image_url, poll_url, timeout}
        """
        # 构造请求参数
        data = {
            '_qrsize': '240',
            'qs': '%3Fsid%3Dxiaomiio%26_json%3Dtrue',
            'callback': 'https://sts.api.io.mi.com/sts',
            '_hasLogo': 'false',
            'sid': 'xiaomiio',
            'serviceParam': '',
            '_locale': 'zh_CN',
            '_dc': str(int(time.time() * 1000))
        }

        r = self.session.get(
            self.LOGIN_URL_ENDPOINT,
            params=data,
            headers=self.headers,
            cookies=self.cookies
        )
        result = self._parse_response(r)

        # 提取二维码图片URL和长轮询URL
        qr_image_url = result.get('qr', '')
        poll_url = result.get('lp', '')
        timeout = result.get('timeout', 300)

        # 从二维码URL中提取ticket（用于缓存标识）
        ticket = ''
        if qr_image_url:
            parsed = parse.urlparse(qr_image_url)
            params = parse.parse_qs(parsed.query)
            tickets = params.get('ticket', [])
            ticket = tickets[0] if tickets else ''

        return {
            'ticket': ticket,
            'qr_image_url': qr_image_url,
            'poll_url': poll_url,
            'timeout': timeout,
        }

    def fetch_qr_image(self, qr_image_url: str) -> bytes:
        """
        获取二维码图片字节流（用于后端代理转发，避免浏览器跨域）
        输入：qr_image_url - 二维码图片URL
        输出：PNG 图片字节流
        """
        r = self.session.get(qr_image_url, headers=self.headers, cookies=self.cookies)
        return r.content

    def poll_once(self, poll_url: str) -> dict:
        """
        长轮询一次扫码状态（对外接口）
        使用长轮询方式，服务端会阻塞直到有状态变化或超时
        输入：poll_url - generate_qr 返回的 poll_url
        输出：统一格式的状态字典 {status, message, token}
        """
        try:
            r = self.session.get(
                poll_url,
                headers=self.headers,
                cookies=self.cookies,
                timeout=35  # 长轮询超时时间，略大于服务端超时
            )
        except requests.exceptions.Timeout:
            # 长轮询超时，表示仍在等待扫码
            return {
                'status': QR_STATUS_WAITING,
                'message': '等待扫码',
                'token': {}
            }
        except requests.exceptions.RequestException as e:
            return {
                'status': QR_STATUS_FAILED,
                'message': f'网络错误: {str(e)}',
                'token': {}
            }

        # 解析响应
        try:
            result = self._parse_response(r)
        except (json.JSONDecodeError, ValueError):
            # 响应非JSON，可能是重定向到登录成功页面
            if r.status_code == 200:
                return {
                    'status': QR_STATUS_FAILED,
                    'message': '收到非JSON响应，可能登录流程异常',
                    'token': {}
                }
            return {
                'status': QR_STATUS_FAILED,
                'message': f'HTTP {r.status_code}',
                'token': {}
            }

        return self._interpret_status(result)

    def _interpret_status(self, result: dict) -> dict:
        """
        解析轮询结果为统一状态格式
        输入：result - 轮询返回的字典
        输出：{status, message, token}
        """
        # 登录成功：包含 userId 和 location
        if result.get('userId') and result.get('location'):
            user_id = result.get('userId')
            pass_token = result.get('passToken')
            ssecurity = result.get('ssecurity')
            nonce = result.get('nonce')
            location = result.get('location', '')

            # 获取 serviceToken
            service_token = self._get_service_token(location, nonce, ssecurity)
            if not service_token:
                return {
                    'status': QR_STATUS_FAILED,
                    'message': '获取 serviceToken 失败',
                    'token': {}
                }

            token = {
                'user_id': str(user_id),
                'pass_token': pass_token,
                'device_id': self.device_id,
                'service_token': service_token,
                'security_token': ssecurity,
                'username': '',
            }
            return {
                'status': QR_STATUS_CONFIRMED,
                'message': '登录成功',
                'token': token
            }

        # 已扫码未确认
        if result.get('child_id') or result.get('cUserId'):
            return {
                'status': QR_STATUS_SCANNED,
                'message': '已扫码，请在手机上确认登录',
                'token': {}
            }

        # 其他情况视为等待中
        code = result.get('code', 0)
        desc = result.get('desc') or result.get('description') or ''

        # 二维码过期
        if code in (70016, 70017):
            return {
                'status': QR_STATUS_EXPIRED,
                'message': '二维码已过期，请重新生成',
                'token': {}
            }

        # 用户取消
        if code == 70013:
            return {
                'status': QR_STATUS_CANCELED,
                'message': '用户已取消登录',
                'token': {}
            }

        return {
            'status': QR_STATUS_WAITING,
            'message': '等待扫码',
            'token': {}
        }

    def _get_service_token(self, location: str, nonce, ssecurity: str) -> str:
        """
        获取 serviceToken
        输入：location - 登录成功后的跳转URL，nonce - 随机数，ssecurity - 安全密钥
        输出：serviceToken 字符串
        算法：SHA1("nonce={nonce}&{ssecurity}") -> Base64 -> 作为 clientSign 参数
        """
        n = f"nonce={str(nonce)}&{ssecurity}"
        sign = base64.b64encode(hashlib.sha1(n.encode()).digest()).decode()
        url = f"{location}&clientSign={parse.quote(sign)}"
        r = self.session.get(
            url,
            headers={**self.headers, 'content-type': 'application/x-www-form-urlencoded'},
            cookies=self.cookies
        )
        return r.cookies.get('serviceToken')
