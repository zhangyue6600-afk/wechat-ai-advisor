# -*- coding: utf-8 -*-
"""
WeChat-AI-Advisor Web 核心后端服务
"""
import os
import sys
import json
import queue
import datetime
from flask import Flask, render_template, request, jsonify, Response, send_from_directory
from core import WeChatAdvisorCore

if getattr(sys, 'frozen', False):
    base_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

template_dir = os.path.join(base_dir, "templates")
static_dir = os.path.join(base_dir, "static")
app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
core = WeChatAdvisorCore(data_dir=os.path.join(os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else base_dir, "data"))

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/wechat/status", methods=["GET"])
def wechat_status():
    res = core.check_wechat_status()
    return jsonify(res)

@app.route("/api/config", methods=["GET", "POST"])
def manage_config():
    if request.method == "POST":
        data = request.json or {}
        api_url = data.get("api_url", "").strip()
        api_key = data.get("api_key", "").strip()
        model = data.get("model", "deepseek-chat").strip()
        temp = float(data.get("temperature", 0.7))
        
        test_ok, test_msg = True, "已跳过连通测试"
        if data.get("test_now", False):
            test_ok, test_msg = core.test_llm_connection(api_url, api_key, model)
            if not test_ok:
                return jsonify({"status": "error", "message": test_msg}), 400
                
        core.save_config({
            "api_url": api_url,
            "api_key": api_key,
            "model": model,
            "temperature": temp
        })
        return jsonify({"status": "success", "message": "配置已保存", "test_msg": test_msg})
    
    cfg = dict(core.llm_config)
    if cfg.get("api_key"):
        # 脱敏展示
        k = cfg["api_key"]
        cfg["masked_key"] = k[:6] + "******" + k[-4:] if len(k) > 10 else "******"
    return jsonify(cfg)

@app.route("/api/models", methods=["POST"])
def get_models():
    data = request.json or {}
    api_url = data.get("api_url", "").strip()
    api_key = data.get("api_key", "").strip()
    models = core.fetch_available_models(api_url, api_key)
    return jsonify({"status": "success", "models": models})

@app.route("/api/chatrooms", methods=["GET"])
def get_chatrooms():
    q = request.args.get("q", "")
    filter_type = request.args.get("type", "all")
    sessions = core.search_sessions(query=q, filter_type=filter_type)
    # 兼容老版字段
    for s in sessions:
        s["room_id"] = s["id"]
        s["room_name"] = s["display_name"]
    return jsonify(sessions)

@app.route("/api/room/scan", methods=["GET"])
def scan_room():
    room_id = request.args.get("room_id", "")
    if not room_id:
        return jsonify({"error": "缺少 room_id 参数"}), 400
    data = core.scan_room_messages(room_id)
    return jsonify(data)

@app.route("/api/room/export", methods=["POST"])
def export_kb():
    data = request.json or {}
    room_id = data.get("room_id")
    room_name = data.get("room_name", "未知群聊")
    days = data.get("days_limit")
    if not room_id:
        return jsonify({"error": "缺少 room_id"}), 400
    res = core.export_knowledge_base(room_id, room_name, days_limit=days)
    return jsonify(res)

@app.route("/api/merge/scan", methods=["POST"])
def scan_merged_rooms():
    data = request.json or {}
    sessions = data.get("sessions", [])
    if not sessions:
        return jsonify({"error": "缺少 sessions 列表"}), 400
    total_messages = 0
    earliest_time = None
    latest_time = None
    detailed_list = []
    for s in sessions:
        rid = s.get("room_id")
        rname = s.get("room_name") or rid
        scan_res = core.scan_room_messages(rid)
        cnt = scan_res.get("total_messages", 0)
        total_messages += cnt
        detailed_list.append({
            "room_id": rid,
            "room_name": rname,
            "total_messages": cnt,
            "earliest_time": scan_res.get("earliest_time"),
            "latest_time": scan_res.get("latest_time")
        })
    return jsonify({
        "status": "success",
        "total_sessions": len(sessions),
        "total_messages": total_messages,
        "details": detailed_list
    })

@app.route("/api/merge/export", methods=["POST"])
def export_merged_kb():
    data = request.json or {}
    sessions = data.get("sessions", [])
    kb_title = data.get("kb_title", "综合技术交流知识库")
    days = data.get("days_limit")
    deep_distill = data.get("deep_distill", False)
    if not sessions:
        return jsonify({"error": "缺少 sessions 列表"}), 400
    
    # 异步执行大模型深度提炼或直接执行
    if deep_distill:
        core.distillation_progress["is_running"] = True
        core.distillation_progress["status"] = "starting"
        core.distillation_progress["current"] = 0
        core.distillation_progress["total"] = 0
        core.distillation_progress["percent"] = 0
        core.distillation_progress["message"] = "正在初始化多群数据并切片..."
        core.distillation_progress["result"] = None
        
        thread = threading.Thread(
            target=core.export_merged_knowledge_base,
            args=(sessions, kb_title, days, True),
            daemon=True
        )
        thread.start()
        return jsonify({"status": "started", "message": "深度提炼已在后台启动"})
    else:
        res = core.export_merged_knowledge_base(sessions, kb_title, days_limit=days, deep_distill=False)
        return jsonify(res)

@app.route("/api/merge/progress", methods=["GET"])
def get_merge_progress():
    return jsonify(core.distillation_progress)

@app.route("/api/download_kb/<filename>")
def download_kb(filename):
    return send_from_directory(core.data_dir, filename, as_attachment=True)

@app.route("/api/monitor/start", methods=["POST"])
def start_monitor():
    data = request.json or {}
    sessions = data.get("sessions")
    room_id = data.get("room_id")
    room_name = data.get("room_name", "微信群")
    clip = data.get("enable_clipboard", True)
    chime = data.get("enable_chime", True)
    
    target = sessions if sessions else room_id
    if not target:
        return jsonify({"error": "缺少要监听的群聊或好友列表"}), 400
    res = core.start_monitoring(target, room_name=room_name, enable_clipboard=clip, enable_chime=chime)
    return jsonify(res)

@app.route("/api/monitor/stop", methods=["POST"])
def stop_monitor():
    res = core.stop_monitoring()
    return jsonify(res)

@app.route("/api/monitor/status", methods=["GET"])
def monitor_status():
    st = core.get_monitor_status()
    st["recent_events"] = core.recent_events[-30:]
    return jsonify(st)

@app.route("/api/monitor/regenerate", methods=["POST"])
def regenerate_advice():
    """重新生成回复建议"""
    data = request.json or {}
    content = data.get("content", "").strip()
    sender = data.get("sender", "群友").strip()
    sname = data.get("session_name", "")
    stype = data.get("session_type", "group")
    if not content:
        return jsonify({"status": "error", "message": "消息内容为空"}), 400
    new_advice = core.regenerate_advice(content, sender, session_name=sname, session_type=stype)
    # 自动更新剪贴板
    try:
        import pyperclip
        pyperclip.copy(new_advice)
    except Exception:
        pass
    return jsonify({"status": "success", "advice": new_advice})

@app.route("/api/monitor/events")
def sse_events():
    """Server-Sent Events 实时事件流"""
    def event_stream():
        q = queue.Queue(maxsize=50)
        def listener_callback(ev):
            try:
                q.put_nowait(ev)
            except Exception:
                pass
        core.event_subscribers.append(listener_callback)
        try:
            while True:
                try:
                    ev = q.get(timeout=25)
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                except queue.Empty:
                    # 心跳保持
                    yield ": ping\n\n"
        finally:
            if listener_callback in core.event_subscribers:
                core.event_subscribers.remove(listener_callback)

    return Response(event_stream(), mimetype="text/event-stream")

if __name__ == "__main__":
    print("=========================================================")
    print("🚀 微信群AI军师 · WeChat-AI-Advisor 已启动！")
    print("👉 请在浏览器中打开: http://127.0.0.1:5000")
    print("=========================================================")
    app.run(host="127.0.0.1", port=5000, debug=False)
