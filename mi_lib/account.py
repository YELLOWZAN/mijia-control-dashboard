"""
小米账号登录模块
负责小米账号的登录认证和Token管理
基于 simple-mi-home 项目适配，改用 .env 配置
"""
import json
import os
import base64
import hashlib
import requests
import requests.utils
from urllib import parse
from mi_lib import utils


class NeedVerifyError(Exception):
    """
    需要二次验证异常
    小米账号开启新设备登录验证时抛出，携带验证URL供用户访问
    """
    def __init__(self, message, verify_url="", device_id=""):
        super().__init__(message)
        self.verify_url = verify_url
        self.device_id = device_id


class TokenStore:
    """
    小米账号Token存储器
    将Token持久化到项目目录下的 .mi_token.json 文件
    """

    def __init__(self):
        """初始化Token存储，设定文件路径并读取已有Token"""
        # 存储到项目目录下，避免用户目录权限问题
        self.token_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.mi_token.json')
        self.token = self._read_token()

    def _read_token(self) -> dict:
        """从文件读取Token，文件不存在时返回空字典"""
        if not os.path.exists(self.token_path):
            return dict()
        try:
            with open(self.token_path, "r", encoding="utf-8") as fp:
                return json.load(fp)
        except (json.JSONDecodeError, IOError):
            return dict()

    def _write_token(self):
        """将当前Token写入文件"""
        with open(self.token_path, "w+", encoding="utf-8") as fp:
            json.dump(self.token, fp)

    def get_file_token(self) -> dict:
        """获取文件中存储的Token"""
        return self._read_token()

    def save_token(self, token: dict):
        """保存Token到文件和内存"""
        self.token = token
        self._write_token()

    def update_token(self, key, value) -> dict:
        """更新Token中的某个字段"""
        self.token[key] = value
        self._write_token()
        return self.token


class MiAccountSession:
    """
    小米账号会话
    负责登录小米账号并管理请求会话
    输入：username - 小米账号，password - 密码，nickname - 昵称
    """

    def __init__(self, username: str, password: str, nickname: str = ""):
        self.username = username
        self.password = password
        self.nickname = nickname
        self.session = requests.Session()
        self.token_store = TokenStore()
        self.token = self.token_store.token
        self.request = self._init_session()

    def _init_session(self) -> requests.Session:
        """
        初始化请求会话
        如果已有完整Token（包含service_token）则直接使用，否则执行登录流程
        输出：配置好Cookie和Header的requests.Session
        异常：NeedVerifyError - 需要二次验证；Exception - 登录失败
        """
        # 检查token是否完整（必须有service_token字段才算登录成功）
        if not self.token or not self.token.get("service_token"):
            self.token = self._login()
            if not self.token:
                raise Exception("登录失败，请检查用户名和密码")
        self.session.headers = {
            'User-Agent': 'APP/com.xiaomi.mihome APPV/6.0.103 iosPassportSDK/3.9.0 iOS/14.4 miHSTS',
            'x-xiaomi-protocal-flag-cli': 'PROTOCAL-HTTP2'
        }
        cookies = {
            'serviceToken': self.token.get("service_token"),
            "userId": self.token.get("user_id"),
            "PassportDeviceId": self.token.get("device_id")
        }
        requests.utils.add_dict_to_cookiejar(self.session.cookies, cookies)
        return self.session

    def _get_login_data(self, device_id: str) -> dict:
        """
        获取登录所需的表单数据
        输入：device_id - 设备ID
        输出：包含登录参数的字典
        """
        url = "https://account.xiaomi.com/pass/serviceLogin?sid=xiaomiio&_json=true"
        headers = {'User-Agent': 'APP/com.xiaomi.mihome APPV/6.0.103 iosPassportSDK/3.9.0 iOS/14.4 miHSTS'}
        cookies = {'sdkVersion': '3.9', 'deviceId': device_id}
        self.login_session.headers = headers
        requests.utils.add_dict_to_cookiejar(self.login_session.cookies, cookies)
        r = self.login_session.get(url)
        result = json.loads(r.text[11:])
        return dict(
            qs=result.get("qs"), sid=result.get("sid"),
            _sign=result.get("_sign"), callback=result.get("callback"),
            user=self.username, hash=utils.get_hash(self.password)
        )

    def _login(self) -> dict:
        """
        执行小米账号登录流程
        输出：登录成功返回Token字典，失败返回空字典
        异常：需要二次验证时抛出 NeedVerifyError（携带验证URL）
        """
        self.login_session = requests.Session()
        url = "https://account.xiaomi.com/pass/serviceLoginAuth2"

        # 生成或复用固定的 device_id，保存到 token 文件中
        # 这样验证状态可以与 device_id 关联，下次登录无需再次验证
        device_id = self.token.get("deviceId") if self.token else None
        if not device_id:
            device_id = utils.get_random(16)
            # 保存 device_id 到文件，即使登录未成功
            self.token_store.update_token("deviceId", device_id)
            self.token = self.token_store.token

        data = self._get_login_data(device_id)
        data["_json"] = "true"
        r = self.login_session.post(url, data=data)
        result = json.loads(r.text[11:])

        code = result.get("code")
        location = result.get("location") or ""
        nonce = result.get("nonce")
        security_token = result.get("ssecurity")
        security_status = result.get("securityStatus", 0)
        result_desc = result.get("desc") or result.get("description") or ""

        # 情况1：直接登录成功，有location且有nonce和ssecurity
        if not code and location and nonce and security_token:
            return self._complete_login(result, device_id, location, nonce, security_token)

        # 情况1b：有location但缺少nonce或ssecurity，仍需验证
        if not code and location and (not nonce or not security_token):
            raise NeedVerifyError(
                "小米账号需要完成新设备验证。",
                verify_url="",
                device_id=device_id
            )

        # 情况2：需要二次验证（securityStatus=16，新设备登录验证）
        if not code and security_status == 16:
            notification_url = result.get("notificationUrl", "")
            # 拼接完整的验证URL
            verify_url = ""
            if notification_url:
                if notification_url.startswith("http"):
                    verify_url = notification_url
                else:
                    verify_url = "https://account.xiaomi.com" + notification_url

            # 抛出异常，携带验证URL供用户访问
            raise NeedVerifyError(
                "小米账号需要完成新设备验证。",
                verify_url=verify_url,
                device_id=device_id
            )

        # 情况3：其他错误（密码错误等）
        if code:
            raise Exception(f"小米账号登录失败: {result_desc} (code={code})")
        return dict()

    def _complete_login(self, result: dict, device_id: str, location: str, nonce, security_token: str) -> dict:
        """
        完成登录流程，获取serviceToken并保存
        输入：result - 登录响应，device_id - 设备ID，location - 跳转地址，
              nonce - 随机数，security_token - 安全Token
        输出：Token字典
        """
        user_id = result.get("userId")
        pass_token = result.get("passToken")
        service_token = self._get_service_token(location, nonce, security_token)
        if not service_token:
            raise Exception("获取serviceToken失败")
        token = {
            "user_id": str(user_id),
            "pass_token": pass_token,
            "device_id": device_id,
            "service_token": service_token,
            "security_token": security_token,
            "username": self.username,
        }
        self.token_store.save_token(token)
        utils.info_log("小米账号登录成功", "登录")
        return token

    def _get_service_token(self, location: str, nonce, security_token: str) -> str:
        """
        获取服务Token
        输入：location - 跳转地址，nonce - 随机数，security_token - 安全Token
        输出：serviceToken字符串
        """
        n = f"nonce={str(nonce)}&{security_token}"
        sign = base64.b64encode(hashlib.sha1(n.encode()).digest()).decode()
        url = f"{location}&clientSign={parse.quote(sign)}"
        r = self.login_session.get(url)
        token = r.cookies.get("serviceToken")
        return token
