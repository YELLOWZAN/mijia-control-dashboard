"""
米家设备服务模块
提供设备列表获取、设备属性读写、设备动作执行等功能
基于 simple-mi-home 项目适配
"""
from mi_lib.account import MiAccountSession
from mi_lib import utils

# 全局设备列表缓存
DEVICES = []


class Device:
    """
    米家设备控制类
    封装单个设备的所有操作（属性读写、动作执行）
    输入：device_id - 设备ID
    """

    def __init__(self, device_id: str, mi_account: MiAccountSession):
        """
        初始化设备控制实例
        输入：device_id - 设备ID，mi_account - 已登录的小米账号会话
        """
        self.mi_account = mi_account
        self.session = mi_account.request
        self.security_token = mi_account.token.get("security_token")
        self.server_url = "https://api.io.mi.com/app"
        self.device_id = device_id

    def device_info(self) -> dict:
        """从全局设备列表中获取当前设备信息"""
        for device in DEVICES:
            if device.get("did") == self.device_id:
                return device
        return dict()

    def http_request(self, uri: str, data: dict = None) -> dict:
        """
        发送HTTP请求到米家API
        输入：uri - 接口路径，data - 请求数据（可选）
        输出：响应JSON字典
        """
        url = self.server_url + uri
        if data:
            params = utils.sign_data(uri, data, self.security_token)
            r = self.session.post(url, data=params)
        else:
            r = self.session.get(url)
        return r.json()

    def do_action(self, sid, aid, values=None, pid="1") -> dict:
        """
        执行设备动作
        输入：sid - 服务ID，aid - 动作ID，values - 参数列表，pid - 属性ID
        输出：统一格式的结果字典 {code, msg, data}
        """
        if values is None:
            values = []
        uri = "/miotspec/action"
        params = dict(params={"did": self.device_id, "siid": sid, "piid": pid, "aiid": aid, "in": values})
        result = self.http_request(uri, params)
        request_code = result.get("code")
        if not request_code:
            data = result.get("result")
            code = result.get("code")
            if not code:
                return dict(code=0, msg="success", data=data)
            else:
                return dict(code=code, msg="error", data=dict())
        return dict(code=request_code, msg="request error", data=dict())

    def get_device_props(self, items: list) -> dict:
        """
        批量获取设备属性
        输入：items - [{"piid": pid, "siid": sid}, ...]
        输出：统一格式的结果字典
        """
        uri = "/miotspec/prop/get"
        params = dict(params=self._add_device_id(items))
        result = self.http_request(uri, params)
        code = result.get("code")
        if not code:
            data = result.get("result")
            return dict(code=0, msg="success", data=data)
        else:
            return dict(code=code, msg=result.get("message"), data=list())

    def get_device_prop(self, sid, pid) -> dict:
        """
        获取单个设备属性
        输入：sid - 服务ID，pid - 属性ID
        输出：统一格式的结果字典
        """
        uri = "/miotspec/prop/get"
        params = dict(params=[{"did": self.device_id, "piid": pid, "siid": sid}])
        result = self.http_request(uri, params)
        code = result.get("code")
        if not code:
            data = result.get("result")[0]
            return dict(code=0, msg="success", data=data)
        else:
            return dict(code=code, msg=result.get("message"), data=dict())

    def set_device_prop(self, sid, pid, value) -> dict:
        """
        设置单个设备属性
        输入：sid - 服务ID，pid - 属性ID，value - 属性值
        输出：统一格式的结果字典
        """
        uri = "/miotspec/prop/set"
        params = dict(params=[{"did": self.device_id, "piid": pid, "siid": sid, "value": value}])
        result = self.http_request(uri, params)
        code = result.get("code")
        if not code:
            data = result.get("result")[0]
            return dict(code=0, msg="success", data=data)
        else:
            return dict(code=code, msg=result.get("message"), data=dict())

    def set_device_props(self, items: list) -> dict:
        """
        批量设置设备属性
        输入：items - [{"piid": pid, "siid": sid, "value": value}, ...]
        输出：统一格式的结果字典
        """
        uri = "/miotspec/prop/set"
        params = dict(params=self._add_device_id(items))
        result = self.http_request(uri, params)
        code = result.get("code")
        if not code:
            data = result.get("result")
            return dict(code=0, msg="success", data=data)
        else:
            return dict(code=code, msg=result.get("message"), data=list())

    def _add_device_id(self, items: list) -> list:
        """为每个参数项添加设备ID"""
        result = list()
        for item in items:
            item["did"] = self.device_id
            result.append(item)
        return result


class MiService:
    """
    米家服务类
    负责账号登录、设备列表管理、设备查找
    输入：username - 小米账号，password - 密码，nickname - 昵称
    """

    def __init__(self, username: str, password: str, nickname: str = ""):
        """
        初始化米家服务，登录账号并获取设备列表
        输入：username - 小米账号，password - 密码，nickname - 昵称
        """
        self.mi_account = MiAccountSession(username, password, nickname)
        self.session = self.mi_account.request
        self.security_token = self.mi_account.token.get("security_token")
        self._account_info = {"username": username, "nickname": nickname}
        global DEVICES
        DEVICES = self._fetch_device_list()

    def get_account_info(self) -> dict:
        """获取当前登录账号信息"""
        return self._account_info

    def find_device(self, device_name: str) -> Device:
        """
        通过设备名称查找设备（模糊匹配）
        输入：device_name - 设备名称（支持部分匹配）
        输出：Device实例
        异常：未找到设备时抛出Exception
        """
        for device in DEVICES:
            name = device.get("name", "")
            if device_name in name:
                return Device(device.get("did"), self.mi_account)
        raise Exception("device not found")

    def use_device(self, device_id: str) -> Device:
        """
        通过设备ID创建设备控制实例
        输入：device_id - 设备ID
        输出：Device实例
        """
        return Device(device_id, self.mi_account)

    def _fetch_device_list(self) -> list:
        """
        从米家服务器获取设备列表
        输出：设备列表
        """
        uri = "/home/device_list"
        params = {"getVirtualModel": False, "getHuamiDevices": 0}
        result = self._http_request(uri, data=params)
        device_list = result.get("result", {}).get("list", [])
        return device_list if device_list else list()

    def get_device_list(self) -> dict:
        """
        获取设备列表（对外接口，刷新设备列表）
        输出：统一格式的结果字典 {code, msg, data}
        """
        global DEVICES
        DEVICES = self._fetch_device_list()
        if DEVICES:
            return dict(code=0, msg="success", data=DEVICES)
        else:
            return dict(code=1, msg="未获取到设备列表", data=list())

    def _http_request(self, uri: str, data: dict = None) -> dict:
        """
        发送HTTP请求到米家API（内部方法）
        输入：uri - 接口路径，data - 请求数据
        输出：响应JSON字典
        """
        url = "https://api.io.mi.com/app" + uri
        if data:
            params = utils.sign_data(uri, data, self.security_token)
            r = self.session.post(url, data=params)
        else:
            r = self.session.get(url)
        return r.json()
