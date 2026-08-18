/**
 * 米家设备控制台 - 前端交互逻辑
 * 功能：鉴权验证、实时时钟、设备列表、设备控制、会话保活、自动登出
 */

// ========== 全局状态 ==========
const TOKEN_KEY = 'auth_token';
const IDLE_TIMEOUT = 15 * 60 * 1000; // 15分钟无操作自动登出（毫秒）
const TOUCH_INTERVAL = 5 * 60 * 1000; // 每5分钟向服务器保活一次

// 当前选中的设备
let currentDevice = null;

// ========== 系统设置状态 ==========
// 默认设置（与服务端 defaults 保持一致）
const DEFAULT_SETTINGS = {
    colorMode: 'light',       // light / dark
    cardOpacity: 100,         // 30-100，百分比
    sidebarOpacity: 100,      // 30-100
    headerOpacity: 100,       // 30-100
    bgBlur: 0,                // 0-20 px
    bgOverlay: 0,             // 0-80，百分比
    pageLimit: 12,            // 4-48，单页卡片数
};

// 运行时设置副本
let appSettings = { ...DEFAULT_SETTINGS };
// 背景图片 dataURL（仅存浏览器 localStorage，不上传服务器）
const BG_IMAGE_KEY = 'bg_image_data_url';
// 当前分页状态
let currentPage = 1;
// 设备列表缓存（用于翻页）
let cachedDevices = [];

// ========== 工具函数 ==========

/**
 * 获取存储的 Token
 */
function getToken() {
    return sessionStorage.getItem(TOKEN_KEY);
}

/**
 * 获取 Authorization 请求头
 */
function authHeaders() {
    return {
        'Authorization': 'Bearer ' + getToken(),
        'Content-Type': 'application/json'
    };
}

/**
 * 显示 Toast 通知
 * @param {string} message - 消息内容
 * @param {string} type - 类型：success/error/info
 */
function showToast(message, type = 'info') {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');

    const styles = {
        success: 'bg-emerald-500',
        error: 'bg-red-500',
        info: 'bg-indigo-500'
    };

    toast.className = `${styles[type]} text-white text-sm px-4 py-3 rounded-lg shadow-lg transition-all duration-300 transform translate-x-full opacity-0`;
    toast.textContent = message;
    container.appendChild(toast);

    // 触发动画
    requestAnimationFrame(() => {
        toast.classList.remove('translate-x-full', 'opacity-0');
    });

    // 3秒后消失
    setTimeout(() => {
        toast.classList.add('translate-x-full', 'opacity-0');
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

/**
 * 跳转到登录页
 */
function redirectToLogin() {
    sessionStorage.removeItem(TOKEN_KEY);
    window.location.href = '/';
}

// ========== 系统设置：加载、应用、保存 ==========

/**
 * 从服务器加载设置
 * 失败时使用默认值
 */
async function loadSettings() {
    try {
        const res = await fetch('/api/settings', { headers: authHeaders() });
        if (res.status === 401) return;
        const data = await res.json();
        if (data.code === 0 && data.data) {
            // 覆盖默认值，数值字段转 Number
            for (const k of Object.keys(DEFAULT_SETTINGS)) {
                const v = data.data[k];
                if (v !== undefined && v !== null && v !== '') {
                    appSettings[k] = (typeof DEFAULT_SETTINGS[k] === 'number') ? Number(v) : v;
                }
            }
        }
    } catch (err) {
        console.error('加载设置失败:', err);
    }
}

/**
 * 将设置同步到设置面板表单
 */
function syncSettingsToForm() {
    // 颜色模式
    const radio = document.querySelector(`input[name="colorMode"][value="${appSettings.colorMode}"]`);
    if (radio) radio.checked = true;
    // 透明度
    document.getElementById('cardOpacity').value = appSettings.cardOpacity;
    document.getElementById('sidebarOpacity').value = appSettings.sidebarOpacity;
    document.getElementById('headerOpacity').value = appSettings.headerOpacity;
    document.getElementById('cardOpacityVal').textContent = appSettings.cardOpacity + '%';
    document.getElementById('sidebarOpacityVal').textContent = appSettings.sidebarOpacity + '%';
    document.getElementById('headerOpacityVal').textContent = appSettings.headerOpacity + '%';
    // 背景模糊/遮罩
    document.getElementById('bgBlur').value = appSettings.bgBlur;
    document.getElementById('bgOverlay').value = appSettings.bgOverlay;
    document.getElementById('bgBlurVal').textContent = appSettings.bgBlur + 'px';
    document.getElementById('bgOverlayVal').textContent = appSettings.bgOverlay + '%';
    // 单页卡片数
    document.getElementById('pageLimit').value = appSettings.pageLimit;
}

/**
 * 应用设置到页面样式（颜色模式、透明度、背景）
 * 通过动态注入 <style> 标签实现，避免直接改 Tailwind 类
 */
function applySettingsStyles() {
    // 1. 颜色模式：通过根元素 class 切换 dark
    const body = document.body;
    if (appSettings.colorMode === 'dark') {
        body.classList.add('dark-mode');
    } else {
        body.classList.remove('dark-mode');
    }

    // 2. 透明度：写入动态样式
    let style = document.getElementById('app-dynamic-style');
    if (!style) {
        style = document.createElement('style');
        style.id = 'app-dynamic-style';
        document.head.appendChild(style);
    }
    const cardOp = appSettings.cardOpacity / 100;
    const sidebarOp = appSettings.sidebarOpacity / 100;
    const headerOp = appSettings.headerOpacity / 100;
    style.textContent = `
        .device-card { opacity: ${cardOp}; }
        aside.sidebar-bar { opacity: ${sidebarOp}; }
        header.top-header { opacity: ${headerOp}; }
    `;

    // 3. 背景图片
    applyBackgroundImage();
}

/**
 * 应用背景图片（从 localStorage 读取 dataURL）
 */
function applyBackgroundImage() {
    // 移除旧背景层
    const old = document.getElementById('bg-layer');
    if (old) old.remove();

    const dataUrl = localStorage.getItem(BG_IMAGE_KEY);
    if (!dataUrl) return;

    const bgLayer = document.createElement('div');
    bgLayer.id = 'bg-layer';
    bgLayer.style.cssText = `
        position: fixed;
        inset: 0;
        z-index: -10;
        background-image: url('${dataUrl}');
        background-size: cover;
        background-position: center;
        background-repeat: no-repeat;
        filter: blur(${appSettings.bgBlur}px);
    `;
    document.body.insertBefore(bgLayer, document.body.firstChild);

    // 遮罩层
    if (appSettings.bgOverlay > 0) {
        const overlay = document.createElement('div');
        overlay.id = 'bg-overlay';
        overlay.style.cssText = `
            position: fixed;
            inset: 0;
            z-index: -9;
            background: rgba(0,0,0,${appSettings.bgOverlay / 100});
        `;
        document.body.insertBefore(overlay, document.body.firstChild);
    } else {
        const oldOv = document.getElementById('bg-overlay');
        if (oldOv) oldOv.remove();
    }
}

/**
 * 保存单项设置到服务器（防抖：500ms 内多次调用合并）
 */
let settingsSaveTimer = null;
function saveSetting(key, value) {
    appSettings[key] = value;
    applySettingsStyles();

    // 防抖保存
    if (settingsSaveTimer) clearTimeout(settingsSaveTimer);
    settingsSaveTimer = setTimeout(async () => {
        try {
            await fetch('/api/settings', {
                method: 'POST',
                headers: authHeaders(),
                body: JSON.stringify({ [key]: value })
            });
        } catch (err) {
            console.error('保存设置失败:', err);
        }
    }, 500);
}

/**
 * 恢复默认设置
 */
async function resetSettings() {
    appSettings = { ...DEFAULT_SETTINGS };
    localStorage.removeItem(BG_IMAGE_KEY);
    syncSettingsToForm();
    applySettingsStyles();
    // 上传全部默认值
    try {
        await fetch('/api/settings', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify(appSettings)
        });
    } catch (err) {
        console.error('重置设置失败:', err);
    }
    showToast('已恢复默认设置', 'success');
}

// ========== 页面切换（设备列表 / 设置页） ==========

/**
 * 切换到设备列表页
 */
function showDevicesPage() {
    // 左栏按钮高亮
    document.getElementById('navDevices').classList.add('bg-indigo-50', 'text-indigo-600');
    document.getElementById('navDevices').classList.remove('text-gray-400');
    document.getElementById('navSettings').classList.remove('bg-indigo-50', 'text-indigo-600');
    document.getElementById('navSettings').classList.add('text-gray-400');
    // 隐藏设置页，显示设备相关元素
    document.getElementById('settingsPage').classList.add('hidden');
    // 显示设备列表区
    document.getElementById('pageTitle').textContent = '设备列表';
    document.getElementById('pageSubtitle').textContent = '点击设备卡片进行控制';
    document.getElementById('refreshBtn').classList.remove('hidden');
    // 恢复设备相关状态
    if (cachedDevices.length > 0) {
        showState('grid');
        renderDevicesPage();
    } else {
        loadDeviceList();
    }
}

/**
 * 切换到设置页
 */
function showSettingsPage() {
    // 左栏按钮高亮
    document.getElementById('navSettings').classList.add('bg-indigo-50', 'text-indigo-600');
    document.getElementById('navSettings').classList.remove('text-gray-400');
    document.getElementById('navDevices').classList.remove('bg-indigo-50', 'text-indigo-600');
    document.getElementById('navDevices').classList.add('text-gray-400');
    // 隐藏设备相关元素
    showState('none');
    document.getElementById('refreshBtn').classList.add('hidden');
    document.getElementById('pageTitle').textContent = '系统设置';
    document.getElementById('pageSubtitle').textContent = '个性化外观与显示偏好';
    // 显示设置页
    document.getElementById('settingsPage').classList.remove('hidden');
    syncSettingsToForm();
}

// ========== 鉴权检查 ==========

/**
 * 页面加载时检查鉴权状态
 */
async function checkAuth() {
    const token = getToken();
    if (!token) {
        redirectToLogin();
        return false;
    }

    try {
        const res = await fetch('/api/auth/check', {
            headers: { 'Authorization': 'Bearer ' + token }
        });
        const data = await res.json();

        if (!data.valid) {
            redirectToLogin();
            return false;
        }

        // 设置用户名
        document.getElementById('userName').textContent = data.user || 'admin';
        document.getElementById('userAvatar').textContent = (data.user || 'A')[0].toUpperCase();
        return true;
    } catch {
        redirectToLogin();
        return false;
    }
}

// ========== 实时时钟 ==========

/**
 * 更新北京时间显示（每秒刷新）
 */
function updateClock() {
    const now = new Date();
    // 使用 Asia/Shanghai 时区格式化
    const beijingTime = now.toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false
    });
    document.getElementById('clock').textContent = beijingTime;
}

// ========== 设备列表 ==========

/**
 * 加载设备列表
 */
async function loadDeviceList() {
    showState('loading');

    // 先检查小米账号连接状态
    try {
        const statusRes = await fetch('/api/status', {
            headers: authHeaders()
        });
        if (statusRes.status === 401) {
            showToast('会话已过期，请重新登录', 'error');
            setTimeout(redirectToLogin, 1500);
            return;
        }
        const statusData = await statusRes.json();

        // 如果未连接，显示对应提示
        if (!statusData.connected) {
            if (statusData.need_verify) {
                // 需要二次验证，设置验证URL
                const verifyLink = document.getElementById('verifyUrlLink');
                if (statusData.verify_url) {
                    verifyLink.href = statusData.verify_url;
                    verifyLink.classList.remove('hidden');
                } else {
                    // 没有验证URL，退回通用提示
                    verifyLink.href = 'https://account.xiaomi.com/';
                }
                showState('verify');
                return;
            } else {
                // 其他连接错误
                showError('无法连接小米账号', statusData.message || '请检查.env配置中的小米账号密码');
                return;
            }
        }
    } catch (err) {
        showError('加载失败', '无法连接到服务器');
        return;
    }

    // 已连接，加载设备列表
    try {
        const res = await fetch('/miapi/get_device_list', {
            headers: authHeaders()
        });

        // 检查鉴权是否过期
        if (res.status === 401) {
            showToast('会话已过期，请重新登录', 'error');
            setTimeout(redirectToLogin, 1500);
            return;
        }

        const data = await res.json();

        if (data.code === 0 && data.data && data.data.length > 0) {
            renderDevices(data.data);
            showState('grid');
        } else {
            showState('empty');
        }
    } catch (err) {
        showError('加载失败', '无法连接到服务器，请检查小米账号配置');
    }
}

/**
 * 重试连接小米账号（密码登录方式）
 */
async function retryConnect() {
    const btn = document.getElementById('retryConnectBtn');
    btn.disabled = true;
    btn.textContent = '连接中...';

    try {
        const res = await fetch('/api/retry-connect', {
            method: 'POST',
            headers: authHeaders()
        });
        const data = await res.json();

        if (data.connected) {
            showToast('连接成功', 'success');
            await loadDeviceList();
        } else {
            if (data.need_verify) {
                const verifyLink = document.getElementById('verifyUrlLink');
                if (data.verify_url) {
                    verifyLink.href = data.verify_url;
                    verifyLink.classList.remove('hidden');
                }
                showToast('仍需验证，推荐使用扫码登录', 'info');
            } else {
                showToast(data.message || '连接失败', 'error');
            }
        }
    } catch (err) {
        showToast('网络错误', 'error');
    } finally {
        btn.disabled = false;
        btn.textContent = '重试密码登录';
    }
}

// ============================================================
// 二维码登录相关功能
// ============================================================

let qrPollTimer = null;      // 轮询定时器
let currentQrTicket = null;  // 当前二维码ticket

/**
 * 切换到二维码登录视图
 */
function showQrLoginView() {
    document.getElementById('verifyDefaultView').classList.add('hidden');
    document.getElementById('qrLoginView').classList.remove('hidden');
    generateQrCode();
}

/**
 * 返回默认提示视图
 */
function backToVerifyDefault() {
    // 停止轮询
    stopQrPolling();
    document.getElementById('qrLoginView').classList.add('hidden');
    document.getElementById('verifyDefaultView').classList.remove('hidden');
}

/**
 * 生成二维码
 */
async function generateQrCode() {
    const qrImage = document.getElementById('qrImage');
    const qrLoading = document.getElementById('qrLoading');
    const qrExpiredMask = document.getElementById('qrExpiredMask');
    const qrStatusText = document.getElementById('qrStatusText');

    // 重置UI
    qrImage.classList.add('hidden');
    qrLoading.classList.remove('hidden');
    qrExpiredMask.classList.add('hidden');
    qrStatusText.textContent = '正在生成二维码...';
    qrStatusText.className = 'text-gray-500 text-sm text-center min-h-[20px]';

    // 停止之前的轮询
    stopQrPolling();

    try {
        const res = await fetch('/miapi/qr/generate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });
        const data = await res.json();

        if (data.code === 0 && data.data) {
            currentQrTicket = data.data.ticket;
            // 设置图片src（通过代理接口加载，需加 /miapi 前缀）
            qrImage.src = '/miapi' + data.data.qr_image_url;
            qrImage.onload = () => {
                qrLoading.classList.add('hidden');
                qrImage.classList.remove('hidden');
                qrStatusText.textContent = '请使用米家APP扫码';
            };
            qrImage.onerror = () => {
                qrLoading.classList.add('hidden');
                qrStatusText.textContent = '二维码加载失败';
                qrStatusText.className = 'text-red-500 text-sm text-center min-h-[20px]';
            };

            // 开始轮询
            startQrPolling();
        } else {
            qrLoading.classList.add('hidden');
            qrStatusText.textContent = data.message || '生成二维码失败';
            qrStatusText.className = 'text-red-500 text-sm text-center min-h-[20px]';
        }
    } catch (err) {
        qrLoading.classList.add('hidden');
        qrStatusText.textContent = '网络错误，请重试';
        qrStatusText.className = 'text-red-500 text-sm text-center min-h-[20px]';
    }
}

/**
 * 开始轮询二维码状态（长轮询方式）
 * 后端会阻塞等待状态变化，收到响应后立即发起下一次请求
 */
let qrPollingActive = false;

function startQrPolling() {
    qrPollingActive = true;
    pollQrStatus();
}

function stopQrPolling() {
    qrPollingActive = false;
    if (qrPollTimer) {
        clearTimeout(qrPollTimer);
        qrPollTimer = null;
    }
}

async function pollQrStatus() {
    if (!qrPollingActive || !currentQrTicket) {
        return;
    }

    try {
        const res = await fetch('/miapi/qr/poll', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticket: currentQrTicket })
        });
        const data = await res.json();

        if (!qrPollingActive) return;

        if (data.code === 0 && data.data) {
            const status = data.data.status;
            const message = data.data.message;
            const statusText = document.getElementById('qrStatusText');

            if (status === 'waiting') {
                statusText.textContent = '请使用米家APP扫码';
                statusText.className = 'text-gray-500 text-sm text-center min-h-[20px]';
                // 继续轮询
                pollQrStatus();
            } else if (status === 'scanned') {
                statusText.textContent = '已扫码，请在手机上确认登录';
                statusText.className = 'text-indigo-600 text-sm text-center min-h-[20px] font-medium';
                // 继续轮询
                pollQrStatus();
            } else if (status === 'confirmed') {
                // 登录成功
                stopQrPolling();
                statusText.textContent = '登录成功！正在加载设备...';
                statusText.className = 'text-emerald-600 text-sm text-center min-h-[20px] font-medium';
                showToast('小米账号登录成功', 'success');
                // 延迟后加载设备列表
                setTimeout(() => loadDeviceList(), 1500);
            } else if (status === 'expired') {
                // 二维码过期
                stopQrPolling();
                statusText.textContent = '二维码已过期';
                statusText.className = 'text-red-500 text-sm text-center min-h-[20px]';
                document.getElementById('qrExpiredMask').classList.remove('hidden');
            } else if (status === 'canceled') {
                stopQrPolling();
                statusText.textContent = '已取消登录';
                statusText.className = 'text-red-500 text-sm text-center min-h-[20px]';
            } else if (status === 'failed') {
                stopQrPolling();
                statusText.textContent = message || '登录失败';
                statusText.className = 'text-red-500 text-sm text-center min-h-[20px]';
            }
        } else {
            // 请求异常，继续轮询
            qrPollTimer = setTimeout(pollQrStatus, 3000);
        }
    } catch (err) {
        // 网络错误，3秒后继续轮询
        console.error('轮询失败:', err);
        if (qrPollingActive) {
            qrPollTimer = setTimeout(pollQrStatus, 3000);
        }
    }
}

/**
 * 渲染设备卡片（含分页）
 * 根据 appSettings.pageLimit 切片显示当前页
 * @param {Array} devices - 设备列表（可选，不传则使用 cachedDevices）
 */
function renderDevices(devices) {
    if (devices) {
        cachedDevices = devices;
        currentPage = 1;
    }
    renderDevicesPage();
}

/**
 * 渲染当前页的设备卡片
 */
function renderDevicesPage() {
    const devices = cachedDevices;
    const grid = document.getElementById('deviceGrid');
    grid.innerHTML = '';

    const limit = appSettings.pageLimit;
    const totalPages = Math.max(1, Math.ceil(devices.length / limit));
    if (currentPage > totalPages) currentPage = totalPages;
    if (currentPage < 1) currentPage = 1;

    const start = (currentPage - 1) * limit;
    const pageDevices = devices.slice(start, start + limit);

    pageDevices.forEach(device => {
        const name = device.name || '未命名设备';
        const did = device.did || '';
        const model = device.model || '';
        const isOnline = device.isOnline !== false;

        // 截断设备ID显示
        const shortId = did.length > 12 ? did.substring(0, 12) + '...' : did;

        const card = document.createElement('div');
        card.className = 'device-card bg-white rounded-xl border border-slate-200 p-5 hover:shadow-lg hover:border-indigo-200 cursor-pointer transition-all duration-200';
        card.innerHTML = `
            <div class="flex items-start justify-between mb-3">
                <div class="w-10 h-10 rounded-lg flex items-center justify-center ${isOnline ? 'bg-emerald-50' : 'bg-slate-100'}">
                    <svg class="w-5 h-5 ${isOnline ? 'text-emerald-500' : 'text-slate-400'}" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2"
                              d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z"/>
                    </svg>
                </div>
                <span class="px-2 py-0.5 rounded-full text-xs font-medium ${isOnline ? 'bg-emerald-50 text-emerald-600' : 'bg-slate-100 text-slate-400'}">
                    ${isOnline ? '在线' : '离线'}
                </span>
            </div>
            <h3 class="font-semibold text-gray-800 text-sm mb-1 truncate" title="${name}">${name}</h3>
            <p class="text-gray-400 text-xs font-mono">ID: ${shortId}</p>
            ${model ? `<p class="text-gray-300 text-xs mt-1 font-mono truncate">${model}</p>` : ''}
        `;

        // 点击卡片打开控制弹窗
        card.addEventListener('click', () => openControlModal(device));

        grid.appendChild(card);
    });

    // 渲染分页控制
    renderPagination(devices.length, totalPages);
}

/**
 * 渲染分页控件
 */
function renderPagination(total, totalPages) {
    // 移除旧分页
    const oldPager = document.getElementById('paginationBar');
    if (oldPager) oldPager.remove();

    if (totalPages <= 1) return;

    const bar = document.createElement('div');
    bar.id = 'paginationBar';
    bar.className = 'flex items-center justify-center gap-2 mt-6';

    // 上一页
    const prevBtn = document.createElement('button');
    prevBtn.textContent = '上一页';
    prevBtn.className = 'px-3 py-1.5 text-xs text-gray-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition disabled:opacity-40 disabled:cursor-not-allowed';
    prevBtn.disabled = currentPage === 1;
    prevBtn.onclick = () => { currentPage--; renderDevicesPage(); };
    bar.appendChild(prevBtn);

    // 页码
    const pageLabel = document.createElement('span');
    pageLabel.className = 'text-xs text-gray-500 px-2';
    pageLabel.textContent = `${currentPage} / ${totalPages} 页 (共 ${total} 个)`;
    bar.appendChild(pageLabel);

    // 下一页
    const nextBtn = document.createElement('button');
    nextBtn.textContent = '下一页';
    nextBtn.className = 'px-3 py-1.5 text-xs text-gray-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition disabled:opacity-40 disabled:cursor-not-allowed';
    nextBtn.disabled = currentPage === totalPages;
    nextBtn.onclick = () => { currentPage++; renderDevicesPage(); };
    bar.appendChild(nextBtn);

    // 插入到 grid 后面
    document.getElementById('deviceGrid').after(bar);
}

/**
 * 切换页面显示状态
 * @param {string} state - loading/empty/grid/error/verify/none
 */
function showState(state) {
    const grid = document.getElementById('deviceGrid');
    const loading = document.getElementById('loadingState');
    const empty = document.getElementById('emptyState');
    const error = document.getElementById('errorState');
    const verify = document.getElementById('verifyState');
    const pager = document.getElementById('paginationBar');

    grid.classList.add('hidden');
    loading.classList.add('hidden');
    empty.classList.add('hidden');
    error.classList.add('hidden');
    verify.classList.add('hidden');
    if (pager) pager.classList.add('hidden');

    if (state === 'loading') loading.classList.remove('hidden');
    else if (state === 'empty') empty.classList.remove('hidden');
    else if (state === 'grid') {
        grid.classList.remove('hidden');
        if (pager) pager.classList.remove('hidden');
    }
    else if (state === 'error') error.classList.remove('hidden');
    else if (state === 'verify') verify.classList.remove('hidden');
    // state === 'none' 时全部隐藏
}

/**
 * 显示错误状态
 */
function showError(title, detail) {
    document.getElementById('errorText').textContent = title;
    document.getElementById('errorDetail').textContent = detail;
    showState('error');
}

// ========== 设备控制弹窗 ==========

/**
 * 打开设备控制弹窗
 * @param {Object} device - 设备信息
 */
/**
 * 打开设备控制弹窗
 * 基于设备spec动态生成控制界面
 */
async function openControlModal(device) {
    currentDevice = device;
    document.getElementById('modalDeviceName').textContent = device.name || '未命名设备';
    document.getElementById('modalDeviceId').textContent = 'ID: ' + (device.did || '---');
    document.getElementById('modalDeviceModel').textContent = 'model: ' + (device.model || '---');

    // 显示弹窗
    document.getElementById('controlModal').classList.remove('hidden');

    // 获取设备spec并生成控制界面（内部会管理 loading/content/error 三态）
    await loadDeviceSpec(device);
}

/**
 * 关闭控制弹窗
 */
function closeControlModal() {
    document.getElementById('controlModal').classList.add('hidden');
    currentDevice = null;
}

/**
 * 显示spec加载中状态
 */
function showSpecLoading() {
    document.getElementById('specLoading').classList.remove('hidden');
    document.getElementById('specContent').classList.add('hidden');
    document.getElementById('specError').classList.add('hidden');
}

/**
 * 显示spec内容
 */
function showSpecContent() {
    document.getElementById('specLoading').classList.add('hidden');
    document.getElementById('specContent').classList.remove('hidden');
    document.getElementById('specError').classList.add('hidden');
}

/**
 * 显示spec错误
 */
function showSpecError(msg) {
    document.getElementById('specLoading').classList.add('hidden');
    document.getElementById('specContent').classList.add('hidden');
    document.getElementById('specError').classList.remove('hidden');
    document.getElementById('specErrorMsg').textContent = msg || '该设备可能未在米家spec库中注册';
}

// 缓存设备spec，避免重复请求
let currentSpec = null;
let currentControlItems = null;

/**
 * 获取设备spec并生成控制界面
 */
async function loadDeviceSpec(device) {
    if (!device.model) {
        showSpecError('设备缺少model信息');
        return;
    }

    showSpecLoading();

    let data;
    try {
        const res = await fetch('/miapi/get_spec', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({ model: device.model })
        });
        data = await res.json();
    } catch (err) {
        showSpecError('网络错误，无法连接服务器');
        return;
    }

    if (data.code !== 0 || !data.data) {
        showSpecError(data.message || '获取设备规格失败');
        return;
    }

    currentSpec = data.data;
    currentControlItems = data.data.control_items || [];

    if (currentControlItems.length === 0) {
        showSpecError('该设备没有可控制的属性或动作');
        return;
    }

    // 渲染控制界面（单独 try-catch，避免渲染错误被误报为网络错误）
    try {
        renderControlItems(currentControlItems);
    } catch (err) {
        console.error('渲染控制项失败:', err);
        showSpecError('控制界面渲染失败: ' + (err && err.message ? err.message : '未知错误'));
        return;
    }
    showSpecContent();

    // 自动加载属性值
    try {
        await loadAllProps();
    } catch (err) {
        console.error('加载属性值失败:', err);
    }
}

/**
 * 根据spec渲染控制项
 */
function renderControlItems(controlItems) {
    const container = document.getElementById('specContent');
    container.innerHTML = '';

    controlItems.forEach(svc => {
        // 服务分区
        const serviceDiv = document.createElement('div');
        serviceDiv.className = 'space-y-3';

        // 服务标题
        const serviceTitle = document.createElement('h4');
        serviceTitle.className = 'text-sm font-semibold text-gray-700 flex items-center gap-2';
        serviceTitle.innerHTML = `<span class="w-1 h-4 bg-indigo-500 rounded-full"></span>${svc.service_name}`;
        serviceDiv.appendChild(serviceTitle);

        // 属性控件
        svc.properties.forEach(prop => {
            const propControl = createPropertyControl(svc.siid, prop);
            if (propControl) {
                serviceDiv.appendChild(propControl);
            }
        });

        // 动作按钮
        if (svc.actions.length > 0) {
            const actionContainer = document.createElement('div');
            actionContainer.className = 'flex flex-wrap gap-2 pt-2';
            svc.actions.forEach(action => {
                const btn = document.createElement('button');
                btn.className = 'px-3 py-1.5 bg-slate-100 hover:bg-slate-200 text-gray-700 text-xs font-medium rounded-lg transition';
                btn.textContent = action.name;
                btn.onclick = () => executeAction(svc.siid, action.aiid, action.name);
                actionContainer.appendChild(btn);
            });
            serviceDiv.appendChild(actionContainer);
        }

        container.appendChild(serviceDiv);
    });
}

/**
 * 根据属性格式创建对应的控件
 */
function createPropertyControl(siid, prop) {
    const access = prop.access || [];
    const canRead = access.includes('read');
    const canWrite = access.includes('write');
    const format = prop.format;
    const valueList = prop.valueList || [];
    const valueRange = prop.valueRange;
    const unit = prop.unit || '';

    const wrapper = document.createElement('div');
    wrapper.className = 'flex items-center justify-between gap-3 py-2';

    // 属性名称
    const label = document.createElement('label');
    label.className = 'text-sm text-gray-600 shrink-0';
    label.textContent = prop.name;
    wrapper.appendChild(label);

    // 数值格式白名单（含所有整数/浮点类型）
    const numericFormats = ['int', 'int8', 'int16', 'int32', 'int64',
                            'uint8', 'uint16', 'uint32', 'uint64', 'float'];
    const isNumeric = numericFormats.includes(format);

    // 根据格式生成控件
    let control;
    const controlId = `prop_${siid}_${prop.piid}`;

    if (format === 'bool' && canWrite) {
        // 开关控件
        control = document.createElement('button');
        control.id = controlId;
        control.className = 'relative inline-flex h-6 w-11 items-center rounded-full transition bg-gray-200';
        control.innerHTML = `<span class="inline-block h-4 w-4 transform rounded-full bg-white transition translate-x-1"></span>`;
        control.onclick = () => toggleBoolProperty(siid, prop.piid, control);
    } else if (valueList.length > 0 && canWrite) {
        // 下拉选择控件
        control = document.createElement('select');
        control.id = controlId;
        control.className = 'px-2 py-1.5 border border-slate-200 rounded-lg text-sm outline-none focus:border-indigo-500 bg-white max-w-[160px]';
        valueList.forEach(vl => {
            const option = document.createElement('option');
            option.value = vl.value;
            option.textContent = vl.description;
            control.appendChild(option);
        });
        control.onchange = () => setProperty(siid, prop.piid, control.value, prop.name);
    } else if (valueRange && canWrite && isNumeric) {
        // 滑块控件：valueRange 可能是数组 [min, max, step] 或字符串 "min,max,step"
        let min = 0, max = 100, step = 1;
        if (Array.isArray(valueRange)) {
            min = parseFloat(valueRange[0]) || 0;
            max = parseFloat(valueRange[1]) || 100;
            step = parseFloat(valueRange[2]) || 1;
        } else if (typeof valueRange === 'string') {
            const range = valueRange.split(',');
            min = parseFloat(range[0]) || 0;
            max = parseFloat(range[1]) || 100;
            step = parseFloat(range[2]) || 1;
        }

        const sliderWrapper = document.createElement('div');
        sliderWrapper.className = 'flex items-center gap-2';

        const slider = document.createElement('input');
        slider.type = 'range';
        slider.id = controlId;
        slider.min = min;
        slider.max = max;
        slider.step = step;
        slider.className = 'w-32 accent-indigo-600';

        const valueLabel = document.createElement('span');
        valueLabel.id = controlId + '_val';
        valueLabel.className = 'text-xs text-gray-500 w-14 text-right';
        valueLabel.textContent = '---';

        slider.oninput = () => { valueLabel.textContent = slider.value + unit; };
        slider.onchange = () => setProperty(siid, prop.piid, slider.value, prop.name);

        sliderWrapper.appendChild(slider);
        sliderWrapper.appendChild(valueLabel);
        control = sliderWrapper;
    } else if (canWrite && (isNumeric || format === 'string')) {
        // 可写但无 valueRange/valueList 的数值或字符串：提供输入框
        control = document.createElement('input');
        control.type = isNumeric ? 'number' : 'text';
        control.id = controlId;
        control.className = 'px-2 py-1 border border-slate-200 rounded-lg text-sm outline-none focus:border-indigo-500 w-32';
        control.placeholder = '---';
        control.onchange = () => setProperty(siid, prop.piid, control.value, prop.name);
    } else {
        // 只读显示
        control = document.createElement('span');
        control.id = controlId;
        control.className = 'text-sm text-gray-800 font-medium';
        control.textContent = canRead ? '---' : '—';
    }

    // 只读属性添加刷新标记
    if (canRead && !canWrite) {
        control.classList.add('text-gray-600');
    }

    wrapper.appendChild(control);
    return wrapper;
}

/**
 * 批量加载所有可读属性的当前值
 */
async function loadAllProps() {
    if (!currentControlItems || !currentDevice) return;

    // 收集所有可读属性
    const propItems = [];
    currentControlItems.forEach(svc => {
        svc.properties.forEach(prop => {
            if (prop.access.includes('read')) {
                propItems.push({ siid: svc.siid, piid: prop.piid });
            }
        });
    });

    if (propItems.length === 0) return;

    try {
        const res = await fetch('/miapi/get_props', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({
                device_id: currentDevice.did,
                params: propItems
            })
        });
        const data = await res.json();

        if (data.code === 0 && data.data && Array.isArray(data.data)) {
            // 更新控件显示
            data.data.forEach((item, index) => {
                if (item.code === 0) {
                    const control = document.getElementById(`prop_${item.siid || propItems[index].siid}_${item.piid || propItems[index].piid}`);
                    if (control) {
                        updateControlValue(control, item.value, propItems[index]);
                    }
                }
            });
        }
    } catch (err) {
        // 静默失败，不影响界面
        console.error('加载属性值失败:', err);
    }
}

/**
 * 更新控件显示的值
 */
function updateControlValue(control, value, propItem) {
    if (!control) return;

    // 查找对应的prop定义
    let propDef = null;
    for (const svc of currentControlItems) {
        const found = svc.properties.find(p => p.piid === propItem.piid);
        if (found) { propDef = found; break; }
    }

    const format = propDef ? propDef.format : '';
    const valueList = propDef ? propDef.valueList : [];
    const unit = propDef ? propDef.unit : '';

    if (control.tagName === 'BUTTON' && format === 'bool') {
        // 开关按钮
        const isOn = value === true || value === 'true' || value === 1 || value === '1';
        if (isOn) {
            control.classList.remove('bg-gray-200');
            control.classList.add('bg-indigo-600');
            control.querySelector('span').classList.remove('translate-x-1');
            control.querySelector('span').classList.add('translate-x-6');
        } else {
            control.classList.add('bg-gray-200');
            control.classList.remove('bg-indigo-600');
            control.querySelector('span').classList.add('translate-x-1');
            control.querySelector('span').classList.remove('translate-x-6');
        }
    } else if (control.tagName === 'SELECT' && valueList.length > 0) {
        // 下拉框
        control.value = value;
    } else if (control.tagName === 'INPUT' && control.type === 'range') {
        // 滑块
        control.value = value;
        const valueLabel = document.getElementById(control.id + '_val');
        if (valueLabel) {
            valueLabel.textContent = value + unit;
        }
    } else if (control.tagName === 'INPUT') {
        // 数值/文本输入框
        control.value = value;
    } else if (control.tagName === 'SPAN') {
        // 只读文本
        let displayValue = value;
        if (format === 'bool') {
            displayValue = (value === true || value === 1) ? '开' : '关';
        }
        control.textContent = displayValue + (unit ? ' ' + unit : '');
    }
}

/**
 * 切换布尔属性（开关）
 */
async function toggleBoolProperty(siid, piid, control) {
    if (!currentDevice) return;

    // 获取当前状态
    const isCurrentlyOn = control.classList.contains('bg-indigo-600');
    const newValue = !isCurrentlyOn;

    // 立即更新UI（乐观更新）
    if (newValue) {
        control.classList.remove('bg-gray-200');
        control.classList.add('bg-indigo-600');
        control.querySelector('span').classList.remove('translate-x-1');
        control.querySelector('span').classList.add('translate-x-6');
    } else {
        control.classList.add('bg-gray-200');
        control.classList.remove('bg-indigo-600');
        control.querySelector('span').classList.add('translate-x-1');
        control.querySelector('span').classList.remove('translate-x-6');
    }

    try {
        const res = await fetch('/miapi/set_prop', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({
                device_id: currentDevice.did,
                sid: siid,
                pid: piid,
                value: newValue
            })
        });
        const data = await res.json();
        if (data.code === 0) {
            showToast('设置成功', 'success');
        } else {
            showToast(data.msg || '设置失败', 'error');
            // 回滚
            if (newValue) {
                control.classList.add('bg-gray-200');
                control.classList.remove('bg-indigo-600');
                control.querySelector('span').classList.add('translate-x-1');
                control.querySelector('span').classList.remove('translate-x-6');
            } else {
                control.classList.remove('bg-gray-200');
                control.classList.add('bg-indigo-600');
                control.querySelector('span').classList.remove('translate-x-1');
                control.querySelector('span').classList.add('translate-x-6');
            }
        }
    } catch (err) {
        showToast('网络错误', 'error');
    }
}

/**
 * 设置属性值
 */
async function setProperty(siid, piid, value, propName) {
    if (!currentDevice) return;

    // 数字转换
    let parsedValue = value;
    if (!isNaN(value) && value !== '') {
        parsedValue = Number(value);
    }

    try {
        const res = await fetch('/miapi/set_prop', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({
                device_id: currentDevice.did,
                sid: siid,
                pid: piid,
                value: parsedValue
            })
        });
        const data = await res.json();
        if (data.code === 0) {
            showToast(`${propName} 设置成功`, 'success');
        } else {
            showToast(data.msg || `${propName} 设置失败`, 'error');
        }
    } catch (err) {
        showToast('网络错误', 'error');
    }
}

/**
 * 执行设备动作
 */
async function executeAction(siid, aiid, actionName) {
    if (!currentDevice) return;

    showToast(`正在执行: ${actionName}...`, 'info');

    try {
        const res = await fetch('/miapi/do_action', {
            method: 'POST',
            headers: authHeaders(),
            body: JSON.stringify({
                device_id: currentDevice.did,
                sid: siid,
                aid: aiid,
                action: []
            })
        });
        const data = await res.json();
        if (data.code === 0) {
            showToast(`${actionName} 执行成功`, 'success');
            // 刷新属性值
            setTimeout(() => loadAllProps(), 1000);
        } else {
            showToast(data.msg || `${actionName} 执行失败`, 'error');
        }
    } catch (err) {
        showToast('网络错误', 'error');
    }
}

// ========== 会话保活与超时 ==========

let idleTimer = null;
let touchTimer = null;

/**
 * 重置空闲计时器（15分钟无操作自动登出）
 */
function resetIdleTimer() {
    clearTimeout(idleTimer);
    idleTimer = setTimeout(async () => {
        showToast('长时间未操作，已自动登出', 'info');
        try {
            await fetch('/api/auth/logout', {
                method: 'POST',
                headers: authHeaders()
            });
        } catch {}
        setTimeout(redirectToLogin, 1500);
    }, IDLE_TIMEOUT);
}

/**
 * 定时向服务器发送保活请求
 */
function startTouchTimer() {
    touchTimer = setInterval(async () => {
        try {
            await fetch('/api/auth/touch', {
                method: 'POST',
                headers: authHeaders()
            });
        } catch {}
    }, TOUCH_INTERVAL);
}

// ========== 登出 ==========

async function logout() {
    try {
        await fetch('/api/auth/logout', {
            method: 'POST',
            headers: authHeaders()
        });
    } catch {}
    redirectToLogin();
}

// ========== 初始化 ==========

async function init() {
    // 鉴权检查
    const authed = await checkAuth();
    if (!authed) return;

    // 启动实时时钟
    updateClock();
    setInterval(updateClock, 1000);

    // 加载并应用系统设置
    await loadSettings();
    syncSettingsToForm();
    applySettingsStyles();

    // 加载设备列表
    await loadDeviceList();

    // 启动空闲检测和保活
    resetIdleTimer();
    startTouchTimer();

    // 绑定用户活动事件（重置空闲计时器）
    ['click', 'keydown', 'mousemove', 'scroll'].forEach(event => {
        document.addEventListener(event, resetIdleTimer, { passive: true });
    });

    // 绑定按钮事件
    document.getElementById('logoutBtn').addEventListener('click', logout);
    document.getElementById('refreshBtn').addEventListener('click', loadDeviceList);

    // 导航按钮：设备 / 设置
    document.getElementById('navDevices').addEventListener('click', showDevicesPage);
    document.getElementById('navSettings').addEventListener('click', showSettingsPage);

    // 弹窗关闭
    document.getElementById('modalCloseBtn').addEventListener('click', closeControlModal);
    document.getElementById('modalOverlay').addEventListener('click', closeControlModal);

    // 重试连接按钮
    document.getElementById('retryConnectBtn').addEventListener('click', retryConnect);
    // 二维码登录相关按钮
    document.getElementById('showQrLoginBtn').addEventListener('click', showQrLoginView);
    document.getElementById('qrRefreshBtn').addEventListener('click', generateQrCode);
    document.getElementById('qrBackBtn').addEventListener('click', backToVerifyDefault);

    // 弹窗内：刷新属性状态、重试获取规格
    document.getElementById('refreshPropsBtn').addEventListener('click', () => {
        if (currentDevice) {
            loadAllProps();
        }
    });
    document.getElementById('specRetryBtn').addEventListener('click', () => {
        if (currentDevice) {
            showSpecLoading();
            loadDeviceSpec(currentDevice);
        }
    });

    // ====== 设置页事件绑定 ======
    bindSettingsEvents();

    // ESC 关闭弹窗
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') closeControlModal();
    });
}

/**
 * 绑定设置面板所有控件事件
 * 设置变更实时应用并防抖保存到服务器
 */
function bindSettingsEvents() {
    // 颜色模式
    document.querySelectorAll('input[name="colorMode"]').forEach(radio => {
        radio.addEventListener('change', e => {
            saveSetting('colorMode', e.target.value);
        });
    });

    // 透明度滑块
    const bindOpacity = (inputId, labelId, key) => {
        const input = document.getElementById(inputId);
        const label = document.getElementById(labelId);
        input.addEventListener('input', () => {
            const v = parseInt(input.value);
            label.textContent = v + '%';
            saveSetting(key, v);
        });
    };
    bindOpacity('cardOpacity', 'cardOpacityVal', 'cardOpacity');
    bindOpacity('sidebarOpacity', 'sidebarOpacityVal', 'sidebarOpacity');
    bindOpacity('headerOpacity', 'headerOpacityVal', 'headerOpacity');

    // 背景模糊
    const bgBlur = document.getElementById('bgBlur');
    const bgBlurVal = document.getElementById('bgBlurVal');
    bgBlur.addEventListener('input', () => {
        const v = parseInt(bgBlur.value);
        bgBlurVal.textContent = v + 'px';
        saveSetting('bgBlur', v);
    });

    // 背景遮罩
    const bgOverlay = document.getElementById('bgOverlay');
    const bgOverlayVal = document.getElementById('bgOverlayVal');
    bgOverlay.addEventListener('input', () => {
        const v = parseInt(bgOverlay.value);
        bgOverlayVal.textContent = v + '%';
        saveSetting('bgOverlay', v);
    });

    // 背景图片文件选择
    document.getElementById('bgImageFile').addEventListener('change', e => {
        const file = e.target.files[0];
        if (!file) return;
        // 文件大小限制 5MB（dataURL 形式存 localStorage）
        if (file.size > 5 * 1024 * 1024) {
            showToast('图片不能超过 5MB', 'error');
            return;
        }
        const reader = new FileReader();
        reader.onload = () => {
            try {
                localStorage.setItem(BG_IMAGE_KEY, reader.result);
                applyBackgroundImage();
                showToast('背景图片已应用', 'success');
            } catch (err) {
                showToast('图片太大，无法保存到本地', 'error');
            }
        };
        reader.onerror = () => showToast('读取图片失败', 'error');
        reader.readAsDataURL(file);
    });

    // 清除背景
    document.getElementById('clearBgBtn').addEventListener('click', () => {
        localStorage.removeItem(BG_IMAGE_KEY);
        document.getElementById('bgImageFile').value = '';
        applyBackgroundImage();
        showToast('已清除背景图片', 'info');
    });

    // 单页卡片数
    const pageLimit = document.getElementById('pageLimit');
    const applyPageLimit = () => {
        let v = parseInt(pageLimit.value);
        if (isNaN(v)) v = DEFAULT_SETTINGS.pageLimit;
        v = Math.max(4, Math.min(48, v));
        pageLimit.value = v;
        saveSetting('pageLimit', v);
        // 实时重新分页
        if (cachedDevices.length > 0) {
            renderDevicesPage();
            showState('grid');
        }
    };
    pageLimit.addEventListener('change', applyPageLimit);
    document.getElementById('pageLimitMinus').addEventListener('click', () => {
        pageLimit.value = Math.max(4, parseInt(pageLimit.value) - 1);
        applyPageLimit();
    });
    document.getElementById('pageLimitPlus').addEventListener('click', () => {
        pageLimit.value = Math.min(48, parseInt(pageLimit.value) + 1);
        applyPageLimit();
    });

    // 恢复默认
    document.getElementById('resetSettingsBtn').addEventListener('click', resetSettings);
}

// 页面加载完成后初始化
document.addEventListener('DOMContentLoaded', init);
