/**
 * WeChat-AI-Advisor 前端交互主脚本
 */

let currentSelectedRoom = null;
let currentExportDays = null;
let isMonitoring = false;
let eventSource = null;
let searchTimer = null;

let currentActiveKbFolder = "";

document.addEventListener("DOMContentLoaded", () => {
  loadConfig();
  checkWeChatStatus();
  searchRooms();
  checkMonitorStatus();
  loadKnowledgeBaseList();

  // 记忆用户当前停留在哪一步，刷新或重启自动恢复
  const savedStep = localStorage.getItem("advisor_active_step") || "step1";
  switchTab(savedStep);

  // 绑定主动问答键盘快捷键 (Enter 发送)
  const kbInput = document.getElementById("kb-chat-input");
  if (kbInput) {
    kbInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendKbQuestion();
      }
    });
  }
});

function switchTab(stepId) {
  currentTab = stepId;
  localStorage.setItem("advisor_active_step", stepId);

  document.querySelectorAll(".tab-pane").forEach(el => el.classList.add("hidden"));
  document.querySelectorAll(".step-tab").forEach(el => el.classList.remove("active"));
  
  const targetPane = document.getElementById("pane-" + stepId);
  const targetBtn = document.getElementById("tab-btn-" + stepId);
  if (targetPane) targetPane.classList.remove("hidden");
  if (targetBtn) targetBtn.classList.add("active");

  // 进入 Step 5 时，主动同步多会话 Tabs 与知识库列表
  if (stepId === "step5") {
    renderSessionTabs();
    filterPanelsBySession(currentActiveSessionId);
    loadKnowledgeBaseList();
  }
}

async function checkWeChatStatus() {
  const badge = document.getElementById("wechat-status-text");
  try {
    const res = await fetch("/api/wechat/status");
    const data = await res.json();
    if (data.status === "connected") {
      badge.innerText = "微信已登录连接";
    } else {
      badge.innerText = "微信未登录或无法读取";
      badge.parentElement.classList.replace("text-emerald-400", "text-amber-400");
    }
  } catch (err) {
    badge.innerText = "服务离线";
  }
}

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const data = await res.json();
    if (data.api_url) document.getElementById("cfg-url").value = data.api_url;
    if (data.model) document.getElementById("cfg-model").value = data.model;
    if (data.temperature) document.getElementById("cfg-temp").value = data.temperature;
    if (data.masked_key) {
      const tip = document.getElementById("cfg-masked-tip");
      tip.innerText = `当前已配置 Key: ${data.masked_key} (留空不修改)`;
      tip.classList.remove("hidden");
    }
  } catch (e) {
    console.error(e);
  }
}

async function autoFetchModels() {
  const url = document.getElementById("cfg-url").value.trim();
  const key = document.getElementById("cfg-key").value.trim();
  const msgEl = document.getElementById("cfg-status-msg");
  if (!url) {
    alert("请先在上方输入接口地址 (API Base URL)！");
    return;
  }
  msgEl.className = "text-xs text-indigo-400 font-medium";
  msgEl.innerText = "🔍 正在探测服务器模型列表...";
  try {
    const res = await fetch("/api/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_url: url, api_key: key })
    });
    const data = await res.json();
    if (data.models && data.models.length > 0) {
      document.getElementById("cfg-model").value = data.models[0];
      msgEl.className = "text-xs text-emerald-400 font-medium";
      msgEl.innerText = `✅ 自动检测到模型: ${data.models[0]} (服务器所有可用: ${data.models.join(", ")})，已自动填入！`;
    } else {
      msgEl.className = "text-xs text-amber-400 font-medium";
      msgEl.innerText = "⚠️ 未能自动枚举到模型，请手动输入模型名称（如 qwen27b）";
    }
  } catch (err) {
    msgEl.className = "text-xs text-rose-400 font-medium";
    msgEl.innerText = "获取失败: " + err.message;
  }
}

async function saveLlmConfig(e) {
  e.preventDefault();
  const btn = document.getElementById("btn-save-cfg");
  const msgEl = document.getElementById("cfg-status-msg");
  btn.disabled = true;
  btn.innerText = "正在测试连接...";
  msgEl.innerText = "";
  
  const payload = {
    api_url: document.getElementById("cfg-url").value.trim(),
    api_key: document.getElementById("cfg-key").value.trim(),
    model: document.getElementById("cfg-model").value.trim(),
    temperature: parseFloat(document.getElementById("cfg-temp").value) || 0.7,
    test_now: true
  };

  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (res.ok) {
      msgEl.className = "text-xs pt-2 text-emerald-400";
      msgEl.innerText = `✅ ${data.test_msg}`;
    } else {
      msgEl.className = "text-xs pt-2 text-rose-400";
      msgEl.innerText = `❌ ${data.message}`;
    }
  } catch (err) {
    msgEl.className = "text-xs pt-2 text-rose-400";
    msgEl.innerText = `❌ 请求错误: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.innerText = "保存并测试连通性";
  }
}

let selectedSessions = [];
let sessionFilter = 'all';
let isMergeMode = false;
let mergeSelectedSessions = [];

function setSessionFilter(type, btn) {
  sessionFilter = type;
  document.querySelectorAll(".session-filter-btn").forEach(b => {
    b.className = "session-filter-btn px-3 py-1.5 rounded-lg text-xs font-medium text-slate-400 hover:text-white transition";
  });
  btn.className = "session-filter-btn active px-3 py-1.5 rounded-lg text-xs font-semibold text-white bg-indigo-600 transition";
  searchRooms();
}

function debounceSearchRooms() {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(searchRooms, 300);
}

async function searchRooms() {
  const q = document.getElementById("room-search-input").value.trim();
  const container = document.getElementById("room-list-container");
  container.innerHTML = `<div class="text-center py-6 text-slate-500 text-xs">正在检索群聊与联系人...</div>`;
  
  try {
    const res = await fetch(`/api/chatrooms?q=${encodeURIComponent(q)}&type=${sessionFilter}`);
    const items = await res.json();
    if (items.length === 0) {
      container.innerHTML = `<div class="text-center py-6 text-slate-500 text-xs">未找到包含 "${q}" 的会话</div>`;
      return;
    }
    
    container.innerHTML = "";
    items.forEach(r => {
      const isSelected = selectedSessions.some(s => s.id === r.id);
      const isCheckedForMerge = mergeSelectedSessions.some(s => s.id === r.id);
      const isGroup = r.type === "group";
      const icon = isGroup ? "👥" : "👤";
      const typeBadge = `<span class="px-1.5 py-0.5 rounded text-[10px] ${isGroup ? 'bg-indigo-900/60 text-indigo-300 border border-indigo-700/50' : 'bg-emerald-900/60 text-emerald-300 border border-emerald-700/50'}">${r.type_label}</span>`;
      
      const item = document.createElement("div");
      item.className = `p-3 rounded-xl bg-slate-950/60 border ${isCheckedForMerge ? 'border-purple-500/80 bg-purple-950/20' : 'border-slate-800/80'} hover:border-indigo-500/50 hover:bg-slate-800/40 transition flex items-center justify-between`;
      
      const addBtnText = isSelected ? "已在监听池" : "+ 加入监听";
      const addBtnClass = isSelected ? "bg-slate-800 text-slate-500 cursor-not-allowed" : "bg-indigo-600 hover:bg-indigo-500 text-white";
      
      const extraActions = `
        <button onclick="selectRoomForKB('${r.id}', '${escapeHtml(r.display_name)}')" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white text-xs transition border border-slate-700 flex items-center space-x-1">
          <span>📚 单群提炼</span>
        </button>
      `;
      
      item.innerHTML = `
        <div class="flex items-center space-x-3 flex-1 min-w-0 pr-3">
          <input type="checkbox" onchange="toggleMergeSession(${JSON.stringify(r).replace(/"/g, '&quot;')}, this.checked)" class="w-4 h-4 rounded text-purple-600 focus:ring-purple-500 bg-slate-900 border-slate-700 cursor-pointer" ${isCheckedForMerge ? 'checked' : ''} title="勾选加入合并知识库">
          <div class="w-8 h-8 rounded-lg bg-slate-900 border border-slate-700 flex items-center justify-center text-sm flex-shrink-0">${icon}</div>
          <div class="min-w-0">
            <div class="flex items-center space-x-2">
              <span class="text-sm font-semibold text-slate-200 truncate">${escapeHtml(r.display_name)}</span>
              ${typeBadge}
            </div>
            <div class="text-[11px] text-slate-500 font-mono truncate">${r.id}</div>
          </div>
        </div>
        <div class="flex items-center space-x-2 flex-shrink-0">
          ${extraActions}
          <button onclick="toggleSessionInPool(${JSON.stringify(r).replace(/"/g, '&quot;')})" class="px-3 py-1 rounded-lg text-xs font-medium transition ${addBtnClass}">
            ${addBtnText}
          </button>
        </div>
      `;
      container.appendChild(item);
    });
  } catch (err) {
    container.innerHTML = `<div class="text-center py-6 text-rose-400 text-xs">获取通讯录失败: ${err.message}</div>`;
  }
}

function toggleSessionInPool(session) {
  const idx = selectedSessions.findIndex(s => s.id === session.id);
  if (idx >= 0) {
    selectedSessions.splice(idx, 1);
  } else {
    selectedSessions.push({
      id: session.id,
      name: session.display_name,
      type: session.type,
      label: session.type_label
    });
  }
  renderSelectedPool();
  searchRooms();
}

function removeSessionFromPool(id) {
  selectedSessions = selectedSessions.filter(s => s.id !== id);
  renderSelectedPool();
  searchRooms();
}

function clearAllSelectedSessions() {
  selectedSessions = [];
  renderSelectedPool();
  searchRooms();
}

function renderSelectedPool() {
  const pool = document.getElementById("selected-sessions-pool");
  const countEl = document.getElementById("monitored-count");
  if (countEl) countEl.innerText = selectedSessions.length;
  if (!pool) return;
  
  if (selectedSessions.length === 0) {
    pool.innerHTML = `<span id="pool-empty-tip" class="text-xs text-slate-600">暂未选择目标，请在下方点击【+ 加入监听】</span>`;
    document.getElementById("monitor-target-name").innerText = "未选择会话";
    return;
  }
  
  pool.innerHTML = "";
  selectedSessions.forEach(s => {
    const isGroup = s.type === "group";
    const tag = document.createElement("div");
    tag.className = `flex items-center space-x-1.5 px-2.5 py-1 rounded-lg text-xs border ${isGroup ? 'bg-indigo-950/70 border-indigo-500/40 text-indigo-200' : 'bg-emerald-950/70 border-emerald-500/40 text-emerald-200'}`;
    tag.innerHTML = `
      <span>${isGroup ? '👥' : '👤'}</span>
      <span class="font-medium">${escapeHtml(s.name)}</span>
      <button onclick="removeSessionFromPool('${s.id}')" class="text-slate-400 hover:text-rose-400 ml-1 font-bold">✕</button>
    `;
    pool.appendChild(tag);
  });
  
  const summary = `${selectedSessions.length} 个会话 (${selectedSessions.filter(s => s.type === 'group').length}群 / ${selectedSessions.filter(s => s.type === 'private').length}私聊)`;
  document.getElementById("monitor-target-name").innerText = summary;
}

function toggleMergeSession(session, isChecked) {
  const idx = mergeSelectedSessions.findIndex(s => s.id === session.id);
  if (isChecked) {
    if (idx < 0) {
      mergeSelectedSessions.push({
        id: session.id,
        name: session.display_name,
        type: session.type,
        label: session.type_label
      });
    }
  } else {
    if (idx >= 0) {
      mergeSelectedSessions.splice(idx, 1);
    }
  }
  updateMergeButtonState();
}

function updateMergeButtonState() {
  const btn = document.getElementById("btn-merged-kb-entry");
  const countEl = document.getElementById("merge-selected-count");
  if (countEl) countEl.innerText = mergeSelectedSessions.length;
  if (btn) {
    btn.disabled = mergeSelectedSessions.length < 2;
  }
}

async function prepareMergedKBExport() {
  if (mergeSelectedSessions.length < 2) {
    alert("请至少勾选 2 个群聊或私聊会话后再进行合并提炼！");
    return;
  }
  isMergeMode = true;
  switchTab("step3");
  
  // 更新 Step 3 界面显示
  document.getElementById("step3-heading").innerText = `合并提炼综合知识库 (${mergeSelectedSessions.length} 个会话)`;
  document.getElementById("step3-desc").innerText = `已选取: ${mergeSelectedSessions.map(s => s.name).join('、')}。将归并多群历史聊天记录并按时间轴去重排版。`;
  
  const mergeTitleBox = document.getElementById("merge-title-container");
  if (mergeTitleBox) {
    mergeTitleBox.classList.remove("hidden");
    const defaultTitle = mergeSelectedSessions[0].name.replace(/[0-9]+.*$/, '') + "_综合技术知识库";
    document.getElementById("merge-custom-title").value = defaultTitle.trim() || "多群合并综合技术知识库";
  }
  
  // 汇总扫描信息
  document.getElementById("scan-msg-count").innerText = "汇总中...";
  document.getElementById("scan-start-time").innerText = "计算中...";
  document.getElementById("scan-end-time").innerText = "计算中...";
  
  try {
    const res = await fetch("/api/merge/scan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessions: mergeSelectedSessions })
    });
    const data = await res.json();
    document.getElementById("scan-msg-count").innerText = (data.total_messages || 0).toLocaleString() + " 条 (多群合计)";
    document.getElementById("scan-start-time").innerText = data.earliest_time || "暂无记录";
    document.getElementById("scan-end-time").innerText = data.latest_time || "暂无记录";
  } catch (err) {
    document.getElementById("scan-msg-count").innerText = "暂无统计";
    document.getElementById("scan-start-time").innerText = "暂无记录";
    document.getElementById("scan-end-time").innerText = "暂无记录";
  }
}

function selectRoomForKB(roomId, roomName) {
  isMergeMode = false;
  const mergeTitleBox = document.getElementById("merge-title-container");
  if (mergeTitleBox) mergeTitleBox.classList.add("hidden");
  
  document.getElementById("step3-heading").innerText = "扫描会话历史记录（群聊 / 私聊均支持）";
  document.getElementById("step3-desc").innerText = "基于 Windows 底层只读文件映射，统计该群聊或好友私聊在本地的完整发言规模与时间分布：";
  
  currentSelectedRoom = { id: roomId, name: roomName };
  switchTab("step3");
  scanCurrentRoom(roomId);
}

function startMultiMonitorFromStep2() {
  if (selectedSessions.length === 0) {
    alert("请先在下方列表中点击【+ 加入监听】选择至少一个群聊或私聊好友！");
    return;
  }
  switchTab("step5");
  if (!isMonitoring) {
    toggleMonitor();
  }
}

function selectRoom(roomId, roomName) {
  selectRoomForKB(roomId, roomName);
}

async function scanCurrentRoom(roomId) {
  document.getElementById("scan-msg-count").innerText = "扫描中...";
  document.getElementById("scan-start-time").innerText = "计算中...";
  document.getElementById("scan-end-time").innerText = "计算中...";
  
  try {
    const res = await fetch(`/api/room/scan?room_id=${encodeURIComponent(roomId)}`);
    const data = await res.json();
    document.getElementById("scan-msg-count").innerText = data.total_messages.toLocaleString() + " 条";
    document.getElementById("scan-start-time").innerText = data.start_time;
    document.getElementById("scan-end-time").innerText = data.end_time;
  } catch (err) {
    document.getElementById("scan-msg-count").innerText = "读取失败";
  }
}

function setExportDays(days, btn) {
  currentExportDays = days;
  document.querySelectorAll(".export-range-btn").forEach(el => el.classList.remove("active"));
  btn.classList.add("active");
}

let distillPollTimer = null;

async function handleStartExport() {
  if (isMergeMode) {
    if (mergeSelectedSessions.length < 2) {
      alert("请至少勾选 2 个群聊或私聊会话！");
      return;
    }
  } else {
    if (!currentSelectedRoom) {
      alert("请先在第 2 步选定群聊或联系人！");
      switchTab("step2");
      return;
    }
  }
  
  const btn = document.getElementById("btn-export-kb");
  const spin = document.getElementById("export-spin");
  const deepDistill = document.getElementById("cfg-deep-distill") ? document.getElementById("cfg-deep-distill").checked : true;
  const progressCard = document.getElementById("distill-progress-card");
  const progressBar = document.getElementById("distill-progress-bar");
  const progressText = document.getElementById("distill-progress-text");
  const progressPercent = document.getElementById("distill-progress-percent");

  btn.disabled = true;
  spin.classList.remove("hidden");
  if (deepDistill && progressCard) {
    progressCard.classList.remove("hidden");
    progressBar.style.width = "0%";
    progressPercent.innerText = "0%";
    progressText.innerText = "正在读取并去噪聊天记录...";
  }

  // 轮询蒸馏进度
  if (deepDistill) {
    if (distillPollTimer) clearInterval(distillPollTimer);
    distillPollTimer = setInterval(async () => {
      try {
        const pRes = await fetch("/api/distill/progress");
        const pData = await pRes.json();
        if (pData) {
          if (progressPercent) progressPercent.innerText = `${pData.percent}%`;
          if (progressBar) progressBar.style.width = `${pData.percent}%`;
          if (progressText) progressText.innerText = pData.message || "大模型提炼中...";
        }
      } catch (e) {}
    }, 1200);
  }

  try {
    let endpoint = "/api/room/export";
    let bodyPayload = {};
    
    if (isMergeMode) {
      endpoint = "/api/merge/export";
      const customTitle = document.getElementById("merge-custom-title").value.trim() || "多群合并综合技术知识库";
      bodyPayload = {
        sessions: mergeSelectedSessions,
        kb_title: customTitle,
        days_limit: currentExportDays,
        deep_distill: deepDistill
      };
    } else {
      bodyPayload = {
        room_id: currentSelectedRoom.id,
        room_name: currentSelectedRoom.name,
        days_limit: currentExportDays,
        deep_distill: deepDistill
      };
    }

    const res = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(bodyPayload)
    });
    const data = await res.json();
    if (res.ok) {
      if (progressPercent) progressPercent.innerText = "100%";
      if (progressBar) progressBar.style.width = "100%";
      if (progressText) progressText.innerText = "提炼与构建完成！";

      const summaryPrefix = isMergeMode ? `【多群综合技术知识库】已生成！` : `知识库提炼完成！`;
      const qaInfo = data.distilled_qa_count ? `，并由大模型提炼出 ${data.distilled_qa_count} 组高价值技术 Q&A 避坑问答对与排错手册` : "";
      document.getElementById("kb-summary-text").innerText = 
        `${summaryPrefix} 共归并 ${data.total_exported.toLocaleString()} 条有效技术交流${qaInfo}，已打包生成双层标准知识库。`;
      const dlBtn = document.getElementById("btn-download-zip");
      dlBtn.href = data.download_url;
      dlBtn.download = data.zip_filename;
      
      setTimeout(() => {
        switchTab("step4");
      }, 800);
    } else {
      alert("提炼导出失败: " + (data.error || "未知错误"));
    }
  } catch (err) {
    alert("提炼异常: " + err.message);
  } finally {
    if (distillPollTimer) clearInterval(distillPollTimer);
    btn.disabled = false;
    spin.classList.add("hidden");
  }
}

async function checkMonitorStatus() {
  try {
    const res = await fetch("/api/monitor/status");
    const data = await res.json();
    if (data.is_monitoring) {
      isMonitoring = true;
      if (data.sessions && data.sessions.length > 0) {
        selectedSessions = data.sessions;
        renderSelectedPool();
      }
      const summary = data.sessions && data.sessions.length > 0 
        ? `${data.sessions.length} 个会话 (${data.sessions.filter(s => s.type === 'group').length}群 / ${data.sessions.filter(s => s.type === 'private').length}私聊)`
        : (data.room_name || "已启动");
      document.getElementById("monitor-target-name").innerText = summary;
      updateMonitorButtonState(true);
      initSSE();
      
      // 填充历史事件
      if (data.recent_events && data.recent_events.length > 0) {
        data.recent_events.forEach(renderIncomingEvent);
      }
    }
  } catch (e) {
    console.error(e);
  }
}

async function toggleMonitor() {
  const btn = document.getElementById("btn-toggle-monitor");
  btn.disabled = true;
  
  if (!isMonitoring) {
    // 开启监控
    if (selectedSessions.length === 0 && currentSelectedRoom) {
      selectedSessions.push({
        id: currentSelectedRoom.id,
        name: currentSelectedRoom.name,
        type: "group",
        label: "👥 群聊"
      });
      renderSelectedPool();
    }
    
    if (selectedSessions.length === 0) {
      alert("请先在第 2 步选择至少一个要监听的群聊或私聊好友！");
      switchTab("step2");
      btn.disabled = false;
      return;
    }
    
    try {
      const res = await fetch("/api/monitor/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sessions: selectedSessions,
          enable_clipboard: document.getElementById("chk-clipboard").checked,
          enable_chime: document.getElementById("chk-chime").checked
        })
      });
      const data = await res.json();
      if (res.ok) {
        isMonitoring = true;
        updateMonitorButtonState(true);
        renderSessionTabs();
        initSSE();
      } else {
        alert("启动监控失败: " + data.error);
      }
    } catch (e) {
      alert("启动监控异常: " + e.message);
    }
  } else {
    // 停止监控
    try {
      await fetch("/api/monitor/stop", { method: "POST" });
      isMonitoring = false;
      updateMonitorButtonState(false);
      if (eventSource) eventSource.close();
    } catch (e) {
      console.error(e);
    }
  }
  btn.disabled = false;
}

function updateMonitorButtonState(active) {
  const btn = document.getElementById("btn-toggle-monitor");
  const icon = document.getElementById("monitor-btn-icon");
  const text = document.getElementById("monitor-btn-text");
  if (active) {
    btn.className = "px-5 py-2 rounded-xl bg-rose-600 hover:bg-rose-500 text-white text-xs font-bold transition shadow-lg shadow-rose-600/30 flex items-center space-x-2";
    icon.innerText = "■";
    text.innerText = "停止当前监控";
  } else {
    btn.className = "px-5 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-bold transition shadow-lg shadow-emerald-600/30 flex items-center space-x-2";
    icon.innerText = "▶";
    text.innerText = "启动 0.8s 极速监控";
  }
}

// -------------------------------------------------------------
// Step 5 多会话独立切换与智能聚焦剪贴板核心状态
// -------------------------------------------------------------
let currentActiveSessionId = "__ALL__"; // 默认聚焦在全部会话
const sessionUnreadCounts = {};        // 各会话未读计数 { sessionId: number }
const sessionMetaStore = {};           // 各会话元数据 { sessionId: { name, type, label } }
const sessionLatestAdvices = {};       // 各会话最新建议 { sessionId: adviceText }

function renderSessionTabs() {
  const container = document.getElementById("dynamic-session-tabs");
  if (!container) return;
  container.innerHTML = "";

  // 将已选会话记录到元数据
  selectedSessions.forEach(s => {
    sessionMetaStore[s.id] = {
      name: s.name,
      type: s.type,
      label: s.type === "group" ? "👥" : "👤"
    };
    if (!(s.id in sessionUnreadCounts)) {
      sessionUnreadCounts[s.id] = 0;
    }
  });

  selectedSessions.forEach(s => {
    const isAct = currentActiveSessionId === s.id;
    const btn = document.createElement("button");
    btn.id = `tab-session-${s.id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
    btn.className = `session-tab-btn px-3.5 py-1.5 rounded-full text-xs font-semibold transition whitespace-nowrap flex items-center space-x-1.5 ${
      isAct 
        ? 'bg-indigo-600 text-white shadow-md shadow-indigo-600/20 border border-indigo-500/50' 
        : 'bg-slate-900/80 hover:bg-slate-800 text-slate-300 border border-slate-800'
    }`;
    btn.onclick = () => selectSessionTab(s.id);
    
    const unread = sessionUnreadCounts[s.id] || 0;
    const badgeHtml = unread > 0 
      ? `<span class="session-unread-badge ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-rose-500 text-white animate-pulse font-bold">${unread}</span>`
      : `<span class="session-unread-badge ml-1 px-1.5 py-0.2 rounded-full text-[10px] bg-slate-800 text-slate-400 hidden">0</span>`;

    btn.innerHTML = `
      <span>${s.type === 'group' ? '👥' : '👤'} ${escapeHtml(s.name)}</span>
      ${badgeHtml}
    `;
    container.appendChild(btn);
  });

  // 更新“全部会话”标签外观
  const allBtn = document.getElementById("tab-all-sessions");
  if (allBtn) {
    if (currentActiveSessionId === "__ALL__") {
      allBtn.className = "session-tab-btn active px-3.5 py-1.5 rounded-full text-xs font-semibold bg-indigo-600 text-white shadow-md shadow-indigo-600/20 border border-indigo-500/50 flex items-center space-x-1.5 transition whitespace-nowrap";
    } else {
      allBtn.className = "session-tab-btn px-3.5 py-1.5 rounded-full text-xs font-semibold bg-slate-900/80 hover:bg-slate-800 text-slate-300 border border-slate-800 flex items-center space-x-1.5 transition whitespace-nowrap";
    }
  }
}

function selectSessionTab(sessionId) {
  currentActiveSessionId = sessionId;

  // 清除该会话的未读红点
  if (sessionId !== "__ALL__") {
    sessionUnreadCounts[sessionId] = 0;
  }
  
  // 重新渲染标签样式
  renderSessionTabs();

  // 更新左右面板标题与聚焦标识
  const streamTitle = document.getElementById("stream-panel-title");
  const cardsTitle = document.getElementById("cards-panel-title");
  const indicator = document.getElementById("active-session-indicator");

  if (sessionId === "__ALL__") {
    if (streamTitle) streamTitle.innerText = "实时发言流 (全部会话)";
    if (cardsTitle) cardsTitle.innerText = "🎯 专家建议生成卡片 (全部会话)";
    if (indicator) indicator.classList.add("hidden");
  } else {
    const meta = sessionMetaStore[sessionId] || { name: sessionId };
    if (streamTitle) streamTitle.innerText = `实时发言流 (${meta.name})`;
    if (cardsTitle) cardsTitle.innerText = `🎯 专属建议卡片 (${meta.name})`;
    if (indicator) {
      indicator.innerText = `聚焦: ${meta.name}`;
      indicator.classList.remove("hidden");
    }

    // 智能聚焦剪贴板：切换到该会话时，若开启了剪贴板同步且该会话有最新建议，自动无缝写入剪切板
    const enableClipboard = document.getElementById("chk-clipboard")?.checked;
    if (enableClipboard && sessionLatestAdvices[sessionId]) {
      const advText = sessionLatestAdvices[sessionId];
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(advText).catch(() => { fallbackCopyText(advText); });
      } else {
        fallbackCopyText(advText);
      }
    }
  }

  // 立即按当前会话过滤消息流与卡片
  filterPanelsBySession(sessionId);
}

function filterPanelsBySession(sessionId) {
  const streamRows = document.querySelectorAll("#live-chat-stream .live-stream-row");
  streamRows.forEach(row => {
    const sid = row.getAttribute("data-session-id");
    if (sessionId === "__ALL__" || sid === sessionId) {
      row.classList.remove("hidden");
    } else {
      row.classList.add("hidden");
    }
  });

  const cardElements = document.querySelectorAll("#advice-cards-container .live-advice-card");
  cardElements.forEach(card => {
    const sid = card.getAttribute("data-session-id");
    if (sessionId === "__ALL__" || sid === sessionId) {
      card.classList.remove("hidden");
    } else {
      card.classList.add("hidden");
    }
  });
}

function initSSE() {
  if (eventSource) eventSource.close();
  eventSource = new EventSource("/api/monitor/events");
  
  eventSource.onmessage = (e) => {
    try {
      const ev = JSON.parse(e.data);
      renderIncomingEvent(ev);
    } catch (err) {}
  };
}

function renderIncomingEvent(ev) {
  const streamContainer = document.getElementById("live-chat-stream");
  const cardsContainer = document.getElementById("advice-cards-container");
  
  // 清理初次提示
  if (streamContainer.children.length === 1 && streamContainer.children[0].classList.contains("text-center")) {
    streamContainer.innerHTML = "";
  }
  if (cardsContainer.children.length === 1 && cardsContainer.children[0].classList.contains("text-center")) {
    cardsContainer.innerHTML = "";
  }
  
  const sid = ev.session_id || "";
  const isGroup = ev.session_type === "group";
  const sessionBadge = ev.session_name ? `<span class="px-1.5 py-0.5 rounded text-[10px] ${isGroup ? 'bg-indigo-950 text-indigo-300 border border-indigo-800' : 'bg-emerald-950 text-emerald-300 border border-emerald-800'} font-mono">${ev.session_label || (isGroup ? '👥 群聊' : '👤 私聊')} ${escapeHtml(ev.session_name)}</span>` : '';
  
  // 记录会话元数据（若动态发现新会话）
  if (sid && !sessionMetaStore[sid]) {
    sessionMetaStore[sid] = {
      name: ev.session_name || sid,
      type: ev.session_type || "group",
      label: isGroup ? "👥" : "👤"
    };
    renderSessionTabs();
  }

  // 1. 追加到左侧消息流 (打上 data-session-id 标记)
  const msgRow = document.createElement("div");
  msgRow.className = "live-stream-row p-2.5 rounded-lg bg-slate-900/80 border border-slate-800/80 flex items-start space-x-2.5 transition";
  msgRow.setAttribute("data-session-id", sid);
  if (currentActiveSessionId !== "__ALL__" && currentActiveSessionId !== sid) {
    msgRow.classList.add("hidden");
  }

  msgRow.innerHTML = `
    <span class="text-[10px] text-slate-500 font-mono whitespace-nowrap mt-0.5">${ev.time}</span>
    <div class="flex-1 min-w-0">
      <div class="flex items-center space-x-1.5 mb-0.5">
        ${sessionBadge}
        <span class="font-semibold text-slate-300 text-xs">${escapeHtml(ev.sender)}:</span>
      </div>
      <span class="text-slate-200 text-xs break-words leading-relaxed">${escapeHtml(ev.content)}</span>
    </div>
  `;
  streamContainer.prepend(msgRow);

  // 2. 更新全部会话计数
  const allCountBadge = document.getElementById("tab-badge-all");
  if (allCountBadge) {
    const totalCount = document.querySelectorAll("#live-chat-stream .live-stream-row").length;
    allCountBadge.innerText = totalCount;
  }
  
  // 3. 如果是高价值讨论且生成了建议，追加到右侧建议卡片
  if (ev.is_question && ev.advice) {
    // 缓存该会话的最新建议
    if (sid) {
      sessionLatestAdvices[sid] = ev.advice;
    }

    // 智能聚焦剪贴板控制：仅当当前处于【全部会话】或正好在【当前活跃会话】时，才将建议写入剪贴板
    const enableClipboard = document.getElementById("chk-clipboard")?.checked;
    if (enableClipboard) {
      if (currentActiveSessionId === "__ALL__" || currentActiveSessionId === sid) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(ev.advice).catch(() => { fallbackCopyText(ev.advice); });
        } else {
          fallbackCopyText(ev.advice);
        }
      }
    }

    // 如果该消息产生在后台会话（非当前聚焦会话），累加未读红点
    if (currentActiveSessionId !== "__ALL__" && currentActiveSessionId !== sid) {
      sessionUnreadCounts[sid] = (sessionUnreadCounts[sid] || 0) + 1;
      renderSessionTabs();
    }

    const cardId = "card-" + Math.random().toString(36).substring(2, 9);
    cardDataStore[cardId] = {
      content: ev.content || "",
      sender: ev.sender || "群友",
      session_name: ev.session_name || "",
      session_type: ev.session_type || "group",
      session_id: sid
    };

    const card = document.createElement("div");
    card.id = cardId;
    card.className = "live-advice-card p-4 rounded-xl bg-indigo-950/20 border border-indigo-500/40 hover:border-indigo-400 transition space-y-2.5 relative group shadow-lg";
    card.setAttribute("data-session-id", sid);
    if (currentActiveSessionId !== "__ALL__" && currentActiveSessionId !== sid) {
      card.classList.add("hidden");
    }
    
    const intentBadge = ev.intent ? `<span class="px-2 py-0.5 rounded text-[10px] bg-amber-500/20 text-amber-300 border border-amber-500/30">${escapeHtml(ev.intent)}</span>` : '';
    const clipboardHint = (currentActiveSessionId === "__ALL__" || currentActiveSessionId === sid) && enableClipboard
      ? `<span class="text-emerald-400 text-[10px] bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-800">✓ 已就绪直接 Ctrl+V</span>`
      : `<span class="text-slate-400 text-[10px] bg-slate-900 px-2 py-0.5 rounded border border-slate-800">需点击复制或切到该Tab</span>`;

    card.innerHTML = `
      <div class="flex items-center justify-between text-xs">
        <div class="flex items-center space-x-2">
          ${sessionBadge}
          <span class="font-bold text-indigo-300 flex items-center space-x-1">
            <span>🔔</span><span>${escapeHtml(ev.sender)}</span>
          </span>
          ${intentBadge}
        </div>
        <span class="text-[10px] text-slate-500 font-mono">${ev.time}</span>
      </div>
      <div class="text-xs text-slate-300 bg-slate-950/60 p-2.5 rounded-lg border border-slate-800/70">
        ${escapeHtml(ev.content)}
      </div>
      <div class="advice-content text-xs text-emerald-300 font-sans leading-relaxed bg-emerald-950/20 p-3 rounded-lg border border-emerald-500/30">
        <span class="font-bold block text-emerald-400 text-[11px] mb-1.5 flex items-center justify-between">
          <span>💡 建议回复:</span>
          ${clipboardHint}
        </span>
        <div class="advice-text whitespace-pre-wrap">${escapeHtml(ev.advice)}</div>
      </div>
      <div class="flex items-center justify-end space-x-2 pt-1">
        <button onclick="regenerateCardAdvice(this, '${cardId}')" class="px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-[11px] font-medium transition flex items-center space-x-1 border border-slate-700">
          <span>🔄 重新生成</span>
        </button>
        <button onclick="copyAdviceText(this, '${cardId}')" class="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-[11px] font-medium transition flex items-center space-x-1 shadow-md shadow-indigo-600/20">
          <span>📋 复制内容</span>
        </button>
      </div>
    `;
    cardsContainer.prepend(card);
  }
}

const cardDataStore = {};

async function regenerateCardAdvice(btn, cardId) {
  const card = document.getElementById(cardId);
  if (!card) return;
  const adviceEl = card.querySelector(".advice-text");
  const origBtnText = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span>⏳ 构思中...</span>`;
  
  const info = cardDataStore[cardId] || {};
  
  try {
    const res = await fetch("/api/monitor/regenerate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: info.content || "",
        sender: info.sender || "群友",
        session_name: info.session_name || "",
        session_type: info.session_type || "group"
      })
    });
    const data = await res.json();
    if (res.ok && data.advice) {
      if (adviceEl) adviceEl.innerText = data.advice;
      copyAdviceText(null, cardId);
      btn.innerHTML = `<span>✅ 已更新</span>`;
      setTimeout(() => {
        btn.disabled = false;
        btn.innerHTML = origBtnText;
      }, 2000);
    } else {
      alert("重新生成失败: " + (data.error || "大模型未返回"));
      btn.disabled = false;
      btn.innerHTML = origBtnText;
    }
  } catch (e) {
    alert("请求异常: " + e.message);
    btn.disabled = false;
    btn.innerHTML = origBtnText;
  }
}

function copyAdviceText(btn, cardId) {
  const card = document.getElementById(cardId);
  if (!card) return;
  const textEl = card.querySelector(".advice-text");
  const text = textEl ? textEl.innerText : "";
  if (!text) return;
  
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(() => {
      fallbackCopyText(text);
    });
  } else {
    fallbackCopyText(text);
  }
  if (btn) {
    const orig = btn.innerHTML;
    btn.innerHTML = `<span>✅ 已复制！</span>`;
    setTimeout(() => { btn.innerHTML = orig; }, 2000);
  }
}

function fallbackCopyText(text) {
  try {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    document.body.removeChild(ta);
  } catch (e) {}
}

function escapeHtml(str) {
  if (!str) return "";
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// ==========================================
// 知识库管理、主动专家问答与增量同步功能
// ==========================================

async function loadKnowledgeBaseList() {
  const select = document.getElementById("select-active-kb");
  if (!select) return;

  try {
    const res = await fetch("/api/kb/list");
    const data = await res.json();
    if (data.status === "success" && data.kbs) {
      if (data.kbs.length === 0) {
        select.innerHTML = `<option value="">暂无知识库 (请先在 Step 3 提炼)</option>`;
        updateKbChatHeader("未选择知识库");
        return;
      }
      
      let html = "";
      data.kbs.forEach((kb, idx) => {
        const isSelected = (currentActiveKbFolder === kb.folder_name) || (!currentActiveKbFolder && idx === 0);
        if (isSelected) currentActiveKbFolder = kb.folder_name;
        const countInfo = kb.total_messages ? `(${kb.total_messages}条记录)` : "";
        html += `<option value="${escapeHtml(kb.folder_name)}" ${isSelected ? "selected" : ""}>${escapeHtml(kb.title)} ${countInfo}</option>`;
      });
      select.innerHTML = html;
      
      const selectedOption = select.options[select.selectedIndex];
      if (selectedOption) {
        updateKbChatHeader(selectedOption.text);
      }
    }
  } catch (err) {
    console.warn("加载知识库列表失败:", err);
  }
}

function handleActiveKbChange(folderName) {
  currentActiveKbFolder = folderName;
  const select = document.getElementById("select-active-kb");
  if (select && select.selectedIndex >= 0) {
    updateKbChatHeader(select.options[select.selectedIndex].text);
  }
}

function updateKbChatHeader(title) {
  const el = document.getElementById("kb-chat-current-title");
  if (el) el.innerText = title;
}

// 增量同步
async function triggerIncrementalSync() {
  if (!currentActiveKbFolder) {
    alert("请先选择一个要同步的知识库！");
    return;
  }
  const btn = document.getElementById("btn-inc-sync");
  const origHtml = btn ? btn.innerHTML : "";
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = `<span>🔄 检查新消息...</span>`;
  }

  try {
    const res = await fetch("/api/kb/incremental_update", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ kb_folder: currentActiveKbFolder })
    });
    const data = await res.json();
    if (res.ok && data.status === "success") {
      alert(`🎉 增量同步完成！\n成功提取并追加了 ${data.new_count} 条最新群聊对话！\n知识库与压缩包已实时同步更新。`);
      loadKnowledgeBaseList();
    } else if (res.ok && data.status === "up_to_date") {
      alert("✅ 该知识库已经是最新状态！\n自上次同步后群内暂无新的技术聊天记录。");
    } else {
      alert("增量同步提示: " + (data.message || data.error || "未知响应"));
    }
  } catch (err) {
    alert("增量同步请求失败: " + err.message);
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

// 专家主动问答侧边抽屉
function toggleKbChatDrawer() {
  const drawer = document.getElementById("kb-chat-drawer");
  if (!drawer) return;
  const isHidden = drawer.classList.contains("hidden");
  if (isHidden) {
    drawer.classList.remove("hidden");
    const input = document.getElementById("kb-chat-input");
    if (input) setTimeout(() => input.focus(), 150);
  } else {
    drawer.classList.add("hidden");
  }
}

async function sendKbQuestion() {
  const input = document.getElementById("kb-chat-input");
  const btn = document.getElementById("btn-send-kb-chat");
  const messagesContainer = document.getElementById("kb-chat-messages");
  if (!input || !messagesContainer) return;

  const question = input.value.trim();
  if (!question) return;

  // 清空输入框
  input.value = "";

  // 1. 渲染用户消息气泡
  const userBubble = document.createElement("div");
  userBubble.className = "flex justify-end";
  userBubble.innerHTML = `
    <div class="max-w-[85%] p-3 rounded-2xl bg-indigo-600 text-white shadow-md leading-relaxed">
      ${escapeHtml(question).replace(/\n/g, "<br>")}
    </div>
  `;
  messagesContainer.appendChild(userBubble);

  // 2. 渲染正在思考的 AI 骨架卡片
  const aiBubble = document.createElement("div");
  aiBubble.className = "flex justify-start";
  aiBubble.innerHTML = `
    <div class="max-w-[90%] p-3.5 rounded-2xl bg-slate-800/90 border border-slate-700/80 text-slate-200 shadow-md space-y-2">
      <div class="flex items-center space-x-2 text-indigo-400 font-semibold text-[11px]">
        <span class="animate-spin text-sm">⏳</span>
        <span>正在检索本地知识库并生成深度解答...</span>
      </div>
      <div class="h-2 bg-slate-700 rounded animate-pulse w-3/4"></div>
      <div class="h-2 bg-slate-700 rounded animate-pulse w-1/2"></div>
    </div>
  `;
  messagesContainer.appendChild(aiBubble);
  messagesContainer.scrollTop = messagesContainer.scrollHeight;

  if (btn) btn.disabled = true;

  try {
    const res = await fetch("/api/kb/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        query: question,
        kb_folder: currentActiveKbFolder
      })
    });
    const data = await res.json();
    if (res.ok && data.answer) {
      const formattedAnswer = formatAdviceMarkdown(data.answer);
      aiBubble.innerHTML = `
        <div class="max-w-[92%] p-3.5 rounded-2xl bg-slate-800/95 border border-slate-700 text-slate-200 shadow-lg space-y-2.5">
          <div class="flex items-center justify-between border-b border-slate-700/70 pb-2">
            <span class="text-[11px] font-bold text-indigo-400 flex items-center space-x-1">
              <span>🎯</span>
              <span>知识库专家解答</span>
            </span>
            <button onclick="copyRawText(this, \`${escapeHtml(data.answer).replace(/`/g, "\\`")}\`)" class="text-[11px] px-2 py-0.5 rounded bg-slate-900 hover:bg-slate-700 text-slate-300 transition">
              📋 复制
            </button>
          </div>
          <div class="leading-relaxed text-xs text-slate-200">
            ${formattedAnswer}
          </div>
        </div>
      `;
    } else {
      aiBubble.innerHTML = `
        <div class="max-w-[90%] p-3.5 rounded-2xl bg-rose-950/40 border border-rose-800/60 text-rose-300">
          ⚠️ 解答失败：${escapeHtml(data.error || "大模型未响应")}
        </div>
      `;
    }
  } catch (err) {
    aiBubble.innerHTML = `
      <div class="max-w-[90%] p-3.5 rounded-2xl bg-rose-950/40 border border-rose-800/60 text-rose-300">
        ⚠️ 网络请求错误：${escapeHtml(err.message)}
      </div>
    `;
  } finally {
    if (btn) btn.disabled = false;
    messagesContainer.scrollTop = messagesContainer.scrollHeight;
  }
}

function clearKbChatHistory() {
  const container = document.getElementById("kb-chat-messages");
  if (!container) return;
  container.innerHTML = `
    <div class="p-3.5 rounded-xl bg-slate-800/60 border border-slate-700/60 text-slate-300 leading-relaxed">
      👋 对话历史已清空。您可以随时在此输入新的技术疑问！
    </div>
  `;
}

function copyRawText(btn, text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(() => fallbackCopyText(text));
  } else {
    fallbackCopyText(text);
  }
  if (btn) {
    const orig = btn.innerText;
    btn.innerText = "✓ 已复制";
    setTimeout(() => { btn.innerText = orig; }, 1800);
  }
}
