"""
小米设备规格（spec）获取模块
从 home.miot-spec.com 获取设备的属性规格定义，用于动态生成控制UI

流程：
1. 通过设备model访问 https://home.miot-spec.com/spec?type={model}
2. 从返回的HTML页面中解析内嵌的JSON数据（tree结构）
3. 提取services/properties/actions定义，包含属性格式、访问权限、值范围等
"""
import json
import re
import requests
import time


# spec缓存：model -> {spec, timestamp}
_spec_cache = {}
_SPEC_CACHE_TTL = 86400  # 缓存24小时


def _fetch_spec_html(model: str) -> str:
    """
    从 home.miot-spec.com 获取设备spec页面HTML
    输入：model - 设备型号（如 pzg.plug.1127）
    输出：HTML页面文本
    """
    url = f"https://home.miot-spec.com/spec?type={model}"
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    r = requests.get(url, timeout=15, headers=headers)
    r.raise_for_status()
    return r.text


def _extract_tree_json(html: str) -> dict:
    """
    从HTML页面中提取tree结构JSON
    页面中内嵌了完整的spec数据，格式为 "tree":{...}
    输入：html - 页面HTML文本
    输出：tree字典（包含services/properties/actions）
    """
    # 查找 "tree":{ 的位置
    tree_start = html.find('"tree":{')
    if tree_start < 0:
        return {}

    # 从tree开始提取JSON，通过括号匹配找到完整JSON
    depth = 0
    start = html.find('{', tree_start)
    if start < 0:
        return {}

    end = start
    for i in range(start, len(html)):
        if html[i] == '{':
            depth += 1
        elif html[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    tree_json_str = html[start:end]
    try:
        return json.loads(tree_json_str)
    except json.JSONDecodeError:
        return {}


def _extract_i18n(html: str) -> dict:
    """
    从HTML页面中提取i18n翻译数据
    包含中文翻译，用于显示属性的友好名称
    输入：html - 页面HTML文本
    输出：i18n字典（如 {"zh_cn": {"service:002":"开关", ...}}）
    """
    # 查找 "i18n":{ 的位置
    i18n_start = html.find('"i18n":{')
    if i18n_start < 0:
        return {}

    depth = 0
    start = html.find('{', i18n_start)
    if start < 0:
        return {}

    end = start
    for i in range(start, len(html)):
        if html[i] == '{':
            depth += 1
        elif html[i] == '}':
            depth -= 1
            if depth == 0:
                end = i + 1
                break

    i18n_json_str = html[start:end]
    try:
        return json.loads(i18n_json_str)
    except json.JSONDecodeError:
        return {}


def _extract_urn(html: str) -> str:
    """
    从HTML页面中提取设备URN
    输入：html - 页面HTML文本
    输出：URN字符串（如 urn:miot-spec-v2:device:outlet:...）
    """
    urn_match = re.search(r'"urn":"(urn:miot-spec-v2[^"]+)"', html)
    return urn_match.group(1) if urn_match else ""


def _apply_i18n(tree: dict, i18n: dict, lang: str = "zh_cn") -> dict:
    """
    将i18n翻译应用到tree结构，替换英文description为中文
    tree中service和valuelist自带i18nKey字段，但property和action没有，需要根据iid拼接：
      service:{siid:03d}
      service:{siid:03d}:property:{piid:03d}
      service:{siid:03d}:action:{aiid:03d}
    输入：tree - spec tree结构，i18n - 翻译数据，lang - 语言
    输出：应用翻译后的tree
    """
    translations = i18n.get(lang, {})

    for svc in tree.get('services', []):
        siid = svc.get('iid')
        # 服务翻译：优先用 i18nKey 字段，否则按 siid 拼接
        svc_i18n_key = svc.get('i18nKey') or (f"service:{siid:03d}" if siid else '')
        if svc_i18n_key and svc_i18n_key in translations:
            svc['description'] = translations[svc_i18n_key]

        for prop in svc.get('properties', []):
            piid = prop.get('iid')
            # 属性翻译：tree 中属性无 i18nKey 字段，按 siid/piid 拼接
            prop_i18n_key = prop.get('i18nKey') or (
                f"service:{siid:03d}:property:{piid:03d}" if (siid and piid) else ''
            )
            if prop_i18n_key and prop_i18n_key in translations:
                prop['description'] = translations[prop_i18n_key]

            # 值列表翻译（valueList 项自带 i18nKey）
            for vl in prop.get('valueList', []):
                vl_i18n_key = vl.get('i18nKey', '')
                if vl_i18n_key and vl_i18n_key in translations:
                    vl['description'] = translations[vl_i18n_key]

        for action in svc.get('actions', []):
            aiid = action.get('iid')
            # 动作翻译：tree 中动作无 i18nKey 字段，按 siid/aiid 拼接
            action_i18n_key = action.get('i18nKey') or (
                f"service:{siid:03d}:action:{aiid:03d}" if (siid and aiid) else ''
            )
            if action_i18n_key and action_i18n_key in translations:
                action['description'] = translations[action_i18n_key]

    return tree


def get_device_spec(model: str, lang: str = "zh_cn") -> dict:
    """
    获取设备规格定义（对外接口，带缓存）
    输入：model - 设备型号，lang - 语言（zh_cn/en）
    输出：{
        urn: 设备URN,
        services: [{
            iid, type, description,
            properties: [{iid, type, description, format, access, valueList, valueRange, unit}],
            actions: [{iid, type, description, in, out}],
            events: [...]
        }]
    }
    """
    # 检查缓存
    cache_key = f"{model}_{lang}"
    cached = _spec_cache.get(cache_key)
    if cached and time.time() - cached['timestamp'] < _SPEC_CACHE_TTL:
        return cached['spec']

    # 获取HTML页面
    html = _fetch_spec_html(model)

    # 提取URN
    urn = _extract_urn(html)

    # 提取tree结构
    tree = _extract_tree_json(html)
    if not tree:
        return {"urn": urn, "services": []}

    # 提取i18n翻译
    i18n = _extract_i18n(html)

    # 应用中文翻译
    tree = _apply_i18n(tree, i18n, lang)

    # 构建结果
    spec = {
        "urn": urn,
        "model": model,
        "services": tree.get('services', [])
    }

    # 写入缓存
    _spec_cache[cache_key] = {
        'spec': spec,
        'timestamp': time.time()
    }

    return spec


def build_control_items(spec: dict) -> list:
    """
    从spec构建前端控制项列表
    过滤掉设备信息服务（siid=1），只保留可操作的服务
    输入：spec - get_device_spec返回的spec
    输出：控制项列表，每项包含 {siid, service_name, properties, actions}
    """
    control_items = []
    for svc in spec.get('services', []):
        siid = svc.get('iid')
        # 跳过设备信息服务（通常siid=1）
        if siid == 1:
            continue

        svc_name = svc.get('description') or svc.get('type', '')
        properties = []
        actions = []

        for prop in svc.get('properties', []):
            access = prop.get('access', [])
            # 只包含可读或可写的属性
            if 'read' in access or 'write' in access:
                properties.append({
                    'piid': prop.get('iid'),
                    'name': prop.get('description') or prop.get('type', ''),
                    'type': prop.get('type', ''),
                    'format': prop.get('format', ''),
                    'access': access,
                    'unit': prop.get('unit', ''),
                    'valueList': prop.get('valueList', []),
                    'valueRange': prop.get('valueRange'),
                })

        for action in svc.get('actions', []):
            actions.append({
                'aiid': action.get('iid'),
                'name': action.get('description') or action.get('type', ''),
                'type': action.get('type', ''),
                'in': action.get('in', []),
                'out': action.get('out', []),
            })

        if properties or actions:
            control_items.append({
                'siid': siid,
                'service_name': svc_name,
                'properties': properties,
                'actions': actions,
            })

    return control_items
