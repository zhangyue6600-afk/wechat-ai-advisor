# -*- coding: utf-8 -*-
"""
微信群 AI 军师 - 极速在线热更新程序 (支持 GitHub 及国内自适应加速通道)
"""
import os
import sys
import json
import urllib.request
import shutil

# 确保在各种 Windows 控制台编码下不崩溃
try:
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
except Exception:
    pass

REPO_USER = "zhangyue6600-afk"
REPO_NAME = "wechat-ai-advisor"
BRANCH = "main"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
INTERNAL_DIR = os.path.join(APP_DIR, "_internal")
TARGET_DIR = INTERNAL_DIR if os.path.isdir(INTERNAL_DIR) else APP_DIR
VERSION_FILE = os.path.join(APP_DIR, "version.json")

def load_local_version():
    if os.path.exists(VERSION_FILE):
        try:
            with open(VERSION_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"version": "1.0.0", "build_date": "未知", "changelog": []}

def fetch_url(url_path):
    urls = [
        f"https://raw.githubusercontent.com/{REPO_USER}/{REPO_NAME}/{BRANCH}/{url_path}",
        f"https://ghproxy.net/https://raw.githubusercontent.com/{REPO_USER}/{REPO_NAME}/{BRANCH}/{url_path}",
        f"https://mirror.ghproxy.com/https://raw.githubusercontent.com/{REPO_USER}/{REPO_NAME}/{BRANCH}/{url_path}"
    ]
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                if resp.getcode() == 200:
                    return resp.read()
        except Exception:
            continue
    return None

UPDATE_FILES = [
    "core.py",
    "app.py",
    "templates/index.html",
    "static/app.js",
    "static/style.css",
    "version.json",
    "README.md"
]

def main():
    print("==================================================================")
    print("🚀 微信群 AI 军师 · 自动检查在线更新程序")
    print(f"📦 官方云端源: https://github.com/{REPO_USER}/{REPO_NAME}")
    print("==================================================================")
    local_ver = load_local_version()
    current_ver = local_ver.get('version', '1.0.0')
    print(f"📌 当前本地版本: v{current_ver} (构建日期: {local_ver.get('build_date', '未知')})")
    
    print("\n🔍 正在连接云端服务器检查最新发布版本...")
    raw_v = fetch_url("version.json")
    if not raw_v:
        print("⚠️ 无法连接到云端更新服务器，请检查网络（或稍后再试）。")
        print("💡 当前本地版本仍可正常离线运行。")
        print("==================================================================")
        return

    try:
        remote_data = json.loads(raw_v.decode("utf-8"))
        remote_ver = remote_data.get("version", current_ver)
    except Exception:
        remote_ver = current_ver

    if remote_ver == current_ver:
        print(f"✅ 您当前使用的已经是最新稳定版 (v{current_ver})！无需更新。")
        print("\n【当前版本功能特性】:")
        for item in local_ver.get("changelog", []):
            print("  " + item)
    else:
        print(f"🎉 发现新版本: v{remote_ver}！")
        print("\n【新版本更新内容】:")
        for item in remote_data.get("changelog", []):
            print("  " + item)
        print("\n⚡ 正在拉取轻量热更新补丁...")
        success_cnt = 0
        for f in UPDATE_FILES:
            content = fetch_url(f)
            if content:
                # 写入目标目录
                dest = os.path.join(TARGET_DIR, f.replace("/", os.sep))
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "wb") as fout:
                    fout.write(content)
                # 若存在根目录同时更新
                if TARGET_DIR != APP_DIR:
                    root_dest = os.path.join(APP_DIR, f.replace("/", os.sep))
                    os.makedirs(os.path.dirname(root_dest), exist_ok=True)
                    with open(root_dest, "wb") as fout:
                        fout.write(content)
                success_cnt += 1
                print(f"  ✓ 已更新: {f}")
        
        # 覆写本地 version.json
        with open(VERSION_FILE, "wb") as fv:
            fv.write(raw_v)
            
        print(f"\n✨ 热更新完成！成功更新 {success_cnt}/{len(UPDATE_FILES)} 个核心组件。")
        print("👉 请重新启动【双击启动.bat】即可畅享最新功能！")
    print("==================================================================")

if __name__ == "__main__":
    main()
