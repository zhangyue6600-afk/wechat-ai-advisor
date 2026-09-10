/**
 * WeChat-AI-Advisor 前端交互主脚本
 */

let currentSelectedRoom = null;
let currentExportDays = null;
let isMonitoring = false;
let eventSource = null;
let searchTimer = null;

document.addEventListener("DOMContentLoaded", () => {
  loadConfig();
  checkWeChatStatus();
  searchRooms();
  checkMonitorStatus();
});

function switchTab(stepId) {
  document.querySelectorAll(".tab-pane").forEach(el => el.classList.add("hidden"));
  document.querySelectorAll(".step-tab").forEach(el => el.classList.remove("active"));
  
  const targetPane = document.getElementById("pane-" + stepId);
  const targetBtn = document.getElementById("tab-btn-" + stepId);
  if (targetPane) targetPane.classList.remove("hidden");
  if (targetBtn) targetBtn.classList.add("active");
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
      const isGroup = r.type === "group";
      const icon = isGroup ? "👥" : "👤";
      const typeBadge = `<span class="px-1.5 py-0.5 rounded text-[10px] ${isGroup ? 'bg-indigo-900/60 text-indigo-300 border border-indigo-700/50' : 'bg-emerald-900/60 text-emerald-300 border border-emerald-700/50'}">${r.type_label}</span>`;
      
      const item = document.createElement("div");
      item.className = "p-3 rounded-xl bg-slate-950/60 border border-slate-800/80 hover:border-indigo-500/50 hover:bg-slate-800/40 transition flex items-center justify-between";
      
      const addBtnText = isSelected ? "已在监听池" : "+ 加入监听";
      const addBtnClass = isSelected ? "bg-slate-800 text-slate-500 cursor-not-allowed" : "bg-indigo-600 hover:bg-indigo-500 text-white";
      
      const extraActions = `
        <button onclick="selectRoomForKB('${r.id}', '${escapeHtml(r.display_name)}')" class="px-2.5 py-1 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white text-xs transition border border-slate-700 flex items-center space-x-1">
          <span>📚 提炼知识库</span>
        </button>
      `;
      
      item.innerHTML = `
        <div class="flex items-center space-x-3 flex-1 min-w-0 pr-3">
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

function selectRoomForKB(roomId, roomName) {
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
  currentSelectedRoom = { id: roomId, name: roomName };
  document.getElementById("selected-room-name").innerText = roomName;
  document.getElementById("selected-room-id").innerText = roomId;
  document.getElementById("selected-room-banner").classList.remove("hidden");
  document.getElementById("monitor-target-name").innerText = roomName;
  
  // 自动切换并触发扫描
  switchTab("step3");
  scanCurrentRoom(roomId);
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

async function startExportKB() {
  if (!currentSelectedRoom) {
    alert("请先在第 2 步选定群聊！");
    switchTab("step2");
    return;
  }
  
  const btn = document.getElementById("btn-export-kb");
  const spin = document.getElementById("export-spin");
  btn.disabled = true;
  spin.classList.remove("hidden");
  
  try {
    const res = await fetch("/api/room/export", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        room_id: currentSelectedRoom.id,
        room_name: currentSelectedRoom.name,
        days_limit: currentExportDays
      })
    });
    const data = await res.json();
    if (res.ok) {
      document.getElementById("kb-summary-text").innerText = 
        `共提取 ${data.total_exported.toLocaleString()} 条有效交流记录，已打包生成双层标准知识库（含 Agent Prompt、专题指南与结构化数据）。`;
      const dlBtn = document.getElementById("btn-download-zip");
      dlBtn.href = data.download_url;
      dlBtn.download = data.zip_filename;
      
      switchTab("step4");
    } else {
      alert("导出失败: " + (data.error || "未知错误"));
    }
  } catch (err) {
    alert("导出异常: " + err.message);
  } finally {
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
  
  const isGroup = ev.session_type === "group";
  const sessionBadge = ev.session_name ? `<span class="px-1.5 py-0.5 rounded text-[10px] ${isGroup ? 'bg-indigo-950 text-indigo-300 border border-indigo-800' : 'bg-emerald-950 text-emerald-300 border border-emerald-800'} font-mono">${ev.session_label || (isGroup ? '👥 群聊' : '👤 私聊')} ${escapeHtml(ev.session_name)}</span>` : '';
  
  // 1. 追加到左侧消息流
  const msgRow = document.createElement("div");
  msgRow.className = "p-2 rounded-lg bg-slate-900/80 border border-slate-800/80 flex items-start space-x-2.5";
  msgRow.innerHTML = `
    <span class="text-[10px] text-slate-500 font-mono whitespace-nowrap mt-0.5">${ev.time}</span>
    <div class="flex-1 min-w-0">
      <div class="flex items-center space-x-1.5 mb-0.5">
        ${sessionBadge}
        <span class="font-semibold text-slate-300 text-xs">${escapeHtml(ev.sender)}:</span>
      </div>
      <span class="text-slate-200 text-xs break-words">${escapeHtml(ev.content)}</span>
    </div>
  `;
  streamContainer.prepend(msgRow);
  
  // 2. 如果是高价值讨论且生成了建议，追加到右侧建议卡片
  if (ev.is_question && ev.advice) {
    const cardId = "card-" + Math.random().toString(36).substring(2, 9);
    cardDataStore[cardId] = {
      content: ev.content || "",
      sender: ev.sender || "群友",
      session_name: ev.session_name || "",
      session_type: ev.session_type || "group"
    };
    const card = document.createElement("div");
    card.id = cardId;
    card.className = "p-4 rounded-xl bg-indigo-950/20 border border-indigo-500/40 hover:border-indigo-400 transition space-y-2.5 relative group shadow-lg";
    
    const intentBadge = ev.intent ? `<span class="px-2 py-0.5 rounded text-[10px] bg-amber-500/20 text-amber-300 border border-amber-500/30">${escapeHtml(ev.intent)}</span>` : '';
    
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
          <span>💡 建议回复 (已自动存入剪贴板):</span>
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
    navigator.clipboard.writeText(text).catch(() => {});
  }
  if (btn) {
    const orig = btn.innerHTML;
    btn.innerHTML = `<span>✅ 已复制！</span>`;
    setTimeout(() => { btn.innerHTML = orig; }, 2000);
  }
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
