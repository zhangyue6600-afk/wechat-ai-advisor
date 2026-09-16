# -*- coding: utf-8 -*-
"""
WeChat-AI-Advisor 核心服务引擎
支持微信4.x/3.x数据库只读解析、聊天记录提取、知识库生成与实时军师监听
"""
import os
import sys
import time
import datetime
import re
import json
import sqlite3
import hashlib
import zipfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import pyperclip
import requests
import zstandard as zstd
import xml.etree.ElementTree as ET
import html
import glob
from typing import List, Dict, Optional, Tuple

dctx = zstd.ZstdDecompressor()

def is_private_ip(url: str) -> bool:
    """检测 URL 是否为内网/本地私有地址"""
    return any(k in url for k in ["127.0.0.1", "localhost", "192.168.", "10.", "172.16.", "172.17.", "172.18.", "172.19.", "172.20.", "172.21.", "172.22.", "172.23.", "172.24.", "172.25.", "172.26.", "172.27.", "172.28.", "172.29.", "172.30.", "172.31."])

def search_bing(query: str, max_results: int = 2) -> str:
    """轻量级网络搜索补充（无需第三方API Key，直连Bing聚合）"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
    }
    try:
        # 清理并提取搜索关键词
        clean_q = re.sub(r"[^\w\s\u4e00-\u9fa5\.-]", " ", query).strip()
        if len(clean_q) < 2:
            return ""
        r = requests.get(f'https://cn.bing.com/search?q={clean_q}', headers=headers, timeout=4)
        blocks = re.findall(r'<li class="b_algo".*?>(.*?)</li>', r.text, re.DOTALL)
        snippets = []
        for b in blocks[:max_results]:
            snippet_m = re.search(r'<p[^>]*>(.*?)</p>', b)
            if snippet_m:
                snip = html.unescape(re.sub(r'<[^>]+>', '', snippet_m.group(1))).strip()
                if len(snip) > 15:
                    snippets.append(f"- {snip}")
        return "\n".join(snippets)
    except Exception:
        return ""

def search_local_kb(query: str, kb_dir: str, max_chars: int = 2500) -> str:
    """在本地已提炼的知识库（包含结构化JSONL、技术专题、问答对）中进行深度语义+时间感知检索"""
    if not query or not query.strip():
        return ""

    stopwords = {
        '这个', '这么', '什么', '怎么', '怎样', '为什么', '有没有', '有没有人', '大家', '你们',
        '我们', '他们', '一个', '一下', '最近', '里面', '请问', '知道', '谁能', '有人', '可以',
        '觉得', '觉得呢', '有谁', '说说', '哪些', '这些', '那些', '是不是', '听说', '据说',
        '聊过', '说是有', '知不知道', '一下这个', '有价值', '信息', '内容'
    }

    # 1. 识别时间意图
    today = datetime.date.today()
    # 获取今天是星期几 (0=周一, 6=周日)
    cur_weekday = today.weekday()
    target_dates = set()

    # 计算最近的周末 (周六和周日)
    if any(w in query for w in ['周末', '周六', '周日', '周天', '星期六', '星期天', '星期日']):
        # 上一个周日
        last_sunday = today - datetime.timedelta(days=(cur_weekday + 1) % 7) if cur_weekday != 6 else today
        last_saturday = last_sunday - datetime.timedelta(days=1)
        target_dates.add(last_saturday.strftime('%Y-%m-%d'))
        target_dates.add(last_sunday.strftime('%Y-%m-%d'))
    if '周五' in query or '星期五' in query:
        diff = (cur_weekday - 4) % 7
        target_dates.add((today - datetime.timedelta(days=diff if diff != 0 else 7)).strftime('%Y-%m-%d'))
    if '周四' in query or '星期四' in query:
        diff = (cur_weekday - 3) % 7
        target_dates.add((today - datetime.timedelta(days=diff if diff != 0 else 7)).strftime('%Y-%m-%d'))
    if '昨天' in query:
        target_dates.add((today - datetime.timedelta(days=1)).strftime('%Y-%m-%d'))
    if '今天' in query:
        target_dates.add(today.strftime('%Y-%m-%d'))

    # 2. 提取中英文实体/核心关键词 (支持中文字段 n-gram 切割，解决无空格分词失效问题)
    en_words = set(re.findall(r'[a-zA-Z0-9_\-\.]{2,}', query))
    ch_blocks = re.findall(r'[一-龥]+', query)
    ch_words = set()
    for block in ch_blocks:
        if len(block) in (2, 3, 4) and block not in stopwords:
            ch_words.add(block)
        for n in (2, 3):
            for i in range(len(block) - n + 1):
                w = block[i:i+n]
                if w not in stopwords and len(w) >= 2:
                    ch_words.add(w)
    keywords = list(en_words | ch_words)

    search_dirs = []
    if os.path.exists(kb_dir):
        search_dirs.append(kb_dir)

    parent_output = os.path.dirname(os.path.abspath(kb_dir))
    if os.path.exists(parent_output):
        for entry in os.listdir(parent_output):
            full_entry = os.path.join(parent_output, entry)
            if os.path.isdir(full_entry) and full_entry not in search_dirs:
                if any(kw in entry for kw in ["综合", "合并", "Merged", "vLLM", "知识库", "富士通"]):
                    search_dirs.append(full_entry)

    matches = []

    for s_dir in search_dirs:
        # A. 检索精选问答对 QA.jsonl
        qa_file = os.path.join(s_dir, "01_精选高价值问答对_QA.jsonl")
        if os.path.exists(qa_file):
            try:
                with open(qa_file, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if not line.strip(): continue
                        qa_obj = json.loads(line)
                        combined = qa_obj.get('question','') + "\n" + qa_obj.get('solution','')
                        score = sum(combined.lower().count(w.lower()) for w in keywords) if keywords else 0
                        if score > 0:
                            matches.append((score * 3.0, f"\xe3\x80\x90\xf0\x9f\x8e\xaf \xe7\xb2\xbe\xe9\x80\x89\xe6\x8a\x80\xe6\x9c\xafFAQ\xe9\x97\xae\xe7\xad\x94\xe5\xaf\xb9\xe3\x80\x91\n\xe9\x97\xae: {qa_obj.get('question','')}\n\xe7\xad\x94: {qa_obj.get('solution','')}"))
            except Exception:
                pass

        # B. 检索五维立体知识库与技术专题手册 Markdown
        target_md_patterns = [
            os.path.join(s_dir, "00_五维立体知识库/*.md"),
            os.path.join(s_dir, "00_技术专题与避坑指南/*.md")
        ]
        for pattern in target_md_patterns:
            for md_file in glob.glob(pattern):
                try:
                    with open(md_file, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read()
                    score = sum(content.lower().count(w.lower()) for w in keywords) if keywords else 0
                    if score > 0:
                        paragraphs = content.split("\n\n")
                        best_p = ""
                        best_score = 0
                        for p in paragraphs:
                            ps = sum(p.lower().count(w.lower()) for w in keywords)
                            if ps > best_score and len(p.strip()) > 25:
                                best_score = ps
                                best_p = p.strip()
                        if best_p:
                            matches.append((best_score * 3.0, f"【📖 知识库专卷: {os.path.basename(md_file)}】\n{best_p}"))
                except Exception:
                    pass

        # C. 深度检索 02_结构化数据_JSONL (支持海量真实发言检索与时间过滤)
        jsonl_dir = os.path.join(s_dir, "02_结构化数据_JSONL")
        if os.path.exists(jsonl_dir):
            for jf in os.listdir(jsonl_dir):
                if jf.endswith(".jsonl"):
                    jpath = os.path.join(jsonl_dir, jf)
                    try:
                        with open(jpath, "r", encoding="utf-8", errors="ignore") as f:
                            for line in f:
                                if not line.strip(): continue
                                m = json.loads(line)
                                c = m.get("content", "")
                                if not c or len(c) < 3 or "<msg>" in c[:20] or "<?xml" in c[:20]:
                                    continue
                                t_str = m.get("time_str", "")
                                s_name = m.get("source_name") or m.get("_source_name") or "群聊"
                                sender = m.get("sender_name") or m.get("sender") or "群友"

                                score = 0.0
                                # 时间过滤与加权
                                if target_dates:
                                    if not any(td in t_str for td in target_dates):
                                        continue
                                    score += 15.0
                                    if len(c) > 20:
                                        score += 5.0

                                # 关键词加权
                                if keywords:
                                    c_lower = c.lower()
                                    for kw in keywords:
                                        if kw.lower() in c_lower:
                                            weight = 5.0 if kw in ("猫哥", "tcat", "显卡", "富士通", "v100", "gaudi2", "mi250", "p800", "屯卡") else 1.5
                                            score += weight

                                if score > 0:
                                    clean_c = c.replace('\n', ' ')[:160]
                                    matches.append((score, f"[{s_name}] [{t_str}] {sender}: {clean_c}"))
                    except Exception:
                        pass

    matches.sort(key=lambda x: x[0], reverse=True)
    if matches:
        unique_results = []
        seen = set()
        for sc, text_item in matches:
            if text_item not in seen:
                seen.add(text_item)
                unique_results.append(text_item)
                if len(unique_results) >= 15:
                    break
        res_str = "\n".join(unique_results)
        return res_str[:max_chars]
    return ""

def parse_real_content(raw_bytes) -> str:
    """对原始消息二进制进行 zstd 解压并解析 XML 引用回复与文本"""
    if not raw_bytes:
        return ""
    if isinstance(raw_bytes, str):
        return raw_bytes
    
    data = bytes(raw_bytes)
    if data.startswith(b"\x28\xb5\x2f\xfd"):
        try:
            data = dctx.decompress(data)
        except Exception:
            pass
            
    text = data.decode("utf-8", errors="ignore").strip()
    
    if "<appmsg" in text or "<msg" in text:
        try:
            start = text.find("<msg")
            if start == -1:
                start = text.find("<appmsg")
            if start != -1:
                xml_str = text[start:]
                root = ET.fromstring(xml_str)
                if root.find(".//img") is not None or root.tag == "img":
                    return "[群友分享了图片/截屏]"
                appmsg = root.find(".//appmsg") if root.tag != "appmsg" else root
                title = appmsg.findtext("title") or "" if appmsg is not None else ""
                refer = root.find(".//refermsg")
                ref_txt = ""
                if refer is not None:
                    ref_nick = refer.findtext("displayname") or ""
                    ref_cnt = (refer.findtext("content") or "").strip()
                    ref_txt = f' (引用@{ref_nick}: "{ref_cnt}")'
                parsed_res = f"{title}{ref_txt}".strip()
                if parsed_res:
                    return parsed_res
        except Exception:
            pass
            
    text = re.sub(r"^([a-zA-Z0-9_-]{3,32}|[^:\r\n]+@chatroom):\s*\n?", "", text).strip()
    return text

def _custom_friendly_content(content, mtype):
    if mtype == 3 or str(mtype) == "3":
        return "[群友分享了图片/截屏]"
    parsed = parse_real_content(content)
    if parsed:
        return parsed
    return f"[{mtype}]"

# 导入底层并挂载增强补丁
from wechatauto.db import WeChatDB, Listener, _md5_hex

WeChatDB._friendly_content = staticmethod(_custom_friendly_content)

def _custom_find_msg_table(self, user, conns):
    target = "Msg_" + _md5_hex(user.encode())
    for conn in reversed(conns):
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (target,),
        ).fetchone()
        if row:
            return conn, target
    return None

WeChatDB._find_msg_table = _custom_find_msg_table

class WeChatAdvisorCore:
    def __init__(self, data_dir: str = "data"):
        self.data_dir = os.path.abspath(data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.db = None
        self.listener = None
        self.monitored_room_id = None
        self.monitored_room_name = None
        self.event_subscribers = []
        self.recent_events = []
        self._nickname_cache = {}
        self._contact_map = {}
        self._name2id_map = {}
        self.distillation_progress = {
            "is_running": False,
            "status": "idle",
            "current": 0,
            "total": 0,
            "percent": 0,
            "message": "",
            "result": None
        }
        self.llm_config = {
            "api_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "model": "deepseek-chat",
            "temperature": 0.7
        }
        self.config_path = os.path.join(self.data_dir, "config.json")
        self._load_config()
        self._init_db()

    def _load_config(self):
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    self.llm_config.update(saved)
            except Exception:
                pass

    def save_config(self, config_dict: dict):
        self.llm_config.update(config_dict)
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(self.llm_config, f, ensure_ascii=False, indent=2)

    def _init_db(self):
        try:
            self.db = WeChatDB()
            self._preload_contact_and_name_map()
            return True, f"已连接微信数据库: {self.db.account_dir}"
        except Exception as e:
            return False, f"未检测到运行中的微信或无法获取密钥: {e}"

    def _preload_contact_and_name_map(self):
        """一次性快速预载联系人与 Name2Id 映射表到内存，极速解析真人人名"""
        if not self.db:
            return
        try:
            conn = self.db._open("contact\\contact.db")
            cur = conn.cursor()
            cur.execute("SELECT username, nick_name, remark FROM contact")
            for r in cur.fetchall():
                u = r["username"]
                rem = (r["remark"] or "").strip()
                nic = (r["nick_name"] or "").strip()
                if rem and nic and rem != nic:
                    disp = f"{rem}({nic})"
                else:
                    disp = rem or nic or u
                self._contact_map[u] = disp
                self._nickname_cache[u] = disp
            conn.close()
        except Exception:
            pass

        try:
            for rel in self.db._message_dbs():
                try:
                    conn = self.db._open(rel)
                    cur = conn.cursor()
                    cur.execute("SELECT rowid, user_name FROM Name2Id")
                    for r in cur.fetchall():
                        rid = r["rowid"]
                        wxid = r["user_name"]
                        if wxid:
                            self._name2id_map[(rel, rid)] = wxid
                            self._name2id_map[(rel, str(rid))] = wxid
                            self._name2id_map[rid] = wxid
                            self._name2id_map[str(rid)] = wxid
                            disp = self._contact_map.get(wxid) or wxid
                            self._nickname_cache[(rel, str(rid))] = disp
                            self._nickname_cache[str(rid)] = disp
                    conn.close()
                except Exception:
                    pass
        except Exception:
            pass

    def get_cached_nickname(self, user, db_rel: str = None) -> str:
        """从内存缓存极速查询昵称，支持按分库 db_rel 定位 Name2Id，打通真实备注与群昵称"""
        if user is None:
            return ""
        s_user = str(user).strip()
        if not s_user:
            return ""

        # 优先按 (db_rel, user) 精确查找
        if db_rel and (db_rel, s_user) in self._nickname_cache:
            return self._nickname_cache[(db_rel, s_user)]

        if s_user in self._nickname_cache:
            return self._nickname_cache[s_user]

        if s_user.isdigit():
            num_id = int(s_user)
            wxid = None
            if db_rel and (db_rel, num_id) in self._name2id_map:
                wxid = self._name2id_map[(db_rel, num_id)]
            elif num_id in self._name2id_map:
                wxid = self._name2id_map[num_id]
            elif s_user in self._name2id_map:
                wxid = self._name2id_map[s_user]

            if wxid:
                disp = self._contact_map.get(wxid) or self._query_contact_name(wxid)
                if db_rel:
                    self._nickname_cache[(db_rel, s_user)] = disp
                self._nickname_cache[s_user] = disp
                return disp

        if s_user in self._contact_map:
            self._nickname_cache[s_user] = self._contact_map[s_user]
            return self._nickname_cache[s_user]

        nick = self._query_contact_name(s_user)
        self._nickname_cache[s_user] = nick or s_user
        return self._nickname_cache[s_user]

    def _query_contact_name(self, wxid: str) -> str:
        """从 contact.db 查找单条联系人备注或昵称"""
        if not self.db or not wxid:
            return wxid
        try:
            conn = self.db._open("contact\\contact.db")
            cur = conn.cursor()
            cur.execute("SELECT nick_name, remark FROM contact WHERE username = ? LIMIT 1", (wxid,))
            r = cur.fetchone()
            conn.close()
            if r:
                rem = (r["remark"] or "").strip()
                nic = (r["nick_name"] or "").strip()
                if rem and nic and rem != nic:
                    return f"{rem}({nic})"
                return rem or nic or wxid
        except Exception:
            pass
        return wxid

    def check_wechat_status(self) -> dict:
        if self.db is None:
            ok, msg = self._init_db()
            if not ok:
                return {"status": "error", "message": msg}
        return {
            "status": "connected",
            "account_dir": self.db.account_dir,
            "account_wxid": getattr(self.db, "account_wxid", "已登录")
        }

    def test_llm_connection(self, api_url: str, api_key: str, model: str) -> Tuple[bool, str]:
        """测试大模型连通性，支持思维链模型与局域网直连"""
        clean_key = api_key.strip() if api_key and api_key.strip() else "EMPTY"
        proxies = {"http": None, "https": None} if is_private_ip(api_url) else None
        
        def _try_request(target_url):
            u = target_url.rstrip("/") + "/chat/completions"
            headers = {
                "Authorization": f"Bearer {clean_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model,
                "messages": [
                    {"role": "user", "content": "请回复'OK'两个字母"}
                ],
                "max_tokens": 128
            }
            t0 = time.time()
            resp = requests.post(u, headers=headers, json=payload, timeout=25, proxies=proxies)
            latency = round((time.time() - t0) * 1000)
            if resp.status_code == 200:
                data = resp.json()
                msg = data["choices"][0]["message"]
                reply = msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or "OK"
                return True, reply.strip(), latency
                
            # 自动探测可用模型列表，给出贴心指导
            avail_models = []
            try:
                m_url = target_url.rstrip("/") + "/models"
                mr = requests.get(m_url, headers={"Authorization": f"Bearer {clean_key}"}, timeout=4, proxies=proxies)
                if mr.status_code == 200:
                    avail_models = [m["id"] for m in mr.json().get("data", []) if "embed" not in m["id"] and "rerank" not in m["id"]]
            except Exception:
                pass
            if avail_models and ("No healthy backend" in resp.text or resp.status_code in (404, 503)):
                return False, f"模型名称 '{model}' 错误或未部署！已自动探测到该服务器可用模型为：{avail_models}。请将【模型名称】改为：{avail_models[0]}！", latency
            return False, f"HTTP {resp.status_code}: {resp.text[:150]}", latency

        try:
            ok, reply, latency = _try_request(api_url)
            if ok:
                return True, f"连接成功！模型响应: {reply[:60]} (延迟: {latency}ms)"
            
            # 如果是 7001 端口失败，自动探测 8000 端口
            if ":7001" in api_url:
                alt_url = api_url.replace(":7001", ":8000")
                try:
                    alt_ok, alt_reply, alt_lat = _try_request(alt_url)
                    if alt_ok:
                        return True, f"7001端口超时，但已成功探测到原生8000端口可用！(响应: {alt_reply[:40]}, 延迟: {alt_lat}ms)。建议将端口改为 8000！"
                except Exception:
                    pass
            return False, f"API请求异常: {reply}"
        except Exception as e:
            # 同样测试 8000 备用端口
            if ":7001" in api_url:
                alt_url = api_url.replace(":7001", ":8000")
                try:
                    alt_ok, alt_reply, alt_lat = _try_request(alt_url)
                    if alt_ok:
                        return True, f"7001代理超时，但原生8000端口通信正常！建议将URL改为 {alt_url} (模型: {model})"
                except Exception:
                    pass
            return False, f"网络请求异常: {str(e)}"

    def fetch_available_models(self, api_url: str, api_key: str = "") -> List[str]:
        """拉取目标服务器的可用模型列表"""
        clean_key = api_key.strip() if api_key and api_key.strip() else "EMPTY"
        proxies = {"http": None, "https": None} if is_private_ip(api_url) else None
        m_url = api_url.rstrip("/") + "/models"
        try:
            mr = requests.get(m_url, headers={"Authorization": f"Bearer {clean_key}"}, timeout=4, proxies=proxies)
            if mr.status_code == 200:
                return [m["id"] for m in mr.json().get("data", []) if "embed" not in m["id"] and "rerank" not in m["id"]]
        except Exception:
            pass
        return []

    def search_sessions(self, query: str = "", filter_type: str = "all") -> List[dict]:
        """搜索电脑微信中的群聊与好友私聊联系人"""
        if self.db is None:
            self._init_db()
        if self.db is None:
            return []
        
        contact_conn = self.db._open("contact\\contact.db")
        cur = contact_conn.cursor()
        q = f"%{query.strip()}%" if query else "%"
        cur.execute("""
            SELECT username, nick_name, remark, local_type 
            FROM contact 
            WHERE username NOT LIKE 'gh_%' 
              AND (nick_name LIKE ? OR remark LIKE ?) 
              AND (local_type IN (1, 2))
            LIMIT 60
        """, (q, q))
        rows = cur.fetchall()
        result = []
        for r in rows:
            uid, nick, remark, ltype = r[0], r[1] or "", r[2] or "", r[3]
            is_group = uid.endswith("@chatroom") or ltype == 2
            stype = "group" if is_group else "private"
            
            if filter_type == "group" and not is_group:
                continue
            if filter_type == "private" and is_group:
                continue
                
            display_name = remark if remark else (nick if nick else uid)
            result.append({
                "id": uid,
                "nick_name": nick,
                "remark": remark,
                "display_name": display_name,
                "type": stype,
                "type_label": "👥 群聊" if is_group else "👤 私聊"
            })
        contact_conn.close()
        return result

    def search_chatrooms(self, query: str = "") -> List[dict]:
        """搜索群聊（向后兼容接口）"""
        rooms = self.search_sessions(query, filter_type="group")
        # 兼容老字段
        for r in rooms:
            r["room_id"] = r["id"]
        return rooms

    def scan_room_messages(self, room_id: str) -> dict:
        """扫描指定群的历史消息总览"""
        if self.db is None:
            self._init_db()
        target = "Msg_" + _md5_hex(room_id.encode())
        total_count = 0
        min_ts = 9999999999
        max_ts = 0
        active_shards = []
        
        for rel in self.db._message_dbs():
            try:
                conn = self.db._open(rel)
                row = conn.execute(f"SELECT count(*), min(create_time), max(create_time) FROM {target}").fetchone()
                if row and row[0] > 0:
                    cnt, c_min, c_max = row[0], row[1], row[2]
                    total_count += cnt
                    if c_min and c_min < min_ts: min_ts = c_min
                    if c_max and c_max > max_ts: max_ts = c_max
                    active_shards.append({"db": rel, "count": cnt})
                conn.close()
            except Exception:
                pass
                
        start_str = datetime.datetime.fromtimestamp(min_ts).strftime("%Y-%m-%d %H:%M") if total_count > 0 else "无记录"
        end_str = datetime.datetime.fromtimestamp(max_ts).strftime("%Y-%m-%d %H:%M") if total_count > 0 else "无记录"
        
        return {
            "room_id": room_id,
            "total_messages": total_count,
            "start_time": start_str,
            "end_time": end_str,
            "shards": active_shards
        }

    def fetch_room_messages(self, room_id: str, room_name: str, min_ctime: int = 0) -> list:
        """从微信本地库中拉取指定会话大于等于 min_ctime 的消息记录"""
        if self.db is None:
            self._init_db()
        target = "Msg_" + _md5_hex(room_id.encode())
        records = []
        is_private = not room_id.endswith("@chatroom")
        for rel in self.db._message_dbs():
            try:
                conn = self.db._open(rel)
                cur = conn.cursor()
                query = f"""
                    SELECT local_id, local_type, real_sender_id, create_time, message_content
                    FROM {target} WHERE create_time >= ? ORDER BY sort_seq ASC
                """
                cur.execute(query, (min_ctime,))
                count = 0
                for r in cur.fetchall():
                    count += 1
                    if count % 3000 == 0:
                        self.distillation_progress["message"] = f"正在极速解密读取 [{room_name}] 聊天流水: 已提取 {len(records)} 条..."
                    lid, ltype, sid, ctime, mcontent = r
                    body = parse_real_content(mcontent)
                    if not body or "拍了拍" in body:
                        continue
                    if is_private:
                        if sid == 2 or sid == 0 or sid == 15:
                            sender_nick = "我"
                        else:
                            sender_nick = room_name
                    else:
                        sender_nick = self.get_cached_nickname(sid, db_rel=rel) or str(sid)
                    records.append({
                        "id": lid,
                        "time": ctime,
                        "create_time": ctime,
                        "datetime": datetime.datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S"),
                        "date": datetime.datetime.fromtimestamp(ctime).strftime("%Y-%m-%d"),
                        "sender": sender_nick,
                        "sender_name": sender_nick,
                        "content": body
                    })
                conn.close()
            except Exception:
                pass
        records.sort(key=lambda x: x["time"])
        return records

    def export_knowledge_base(self, room_id: str, room_name: str, days_limit: Optional[int] = None) -> dict:
        """全量或限期导出聊天记录并自动构建知识库压缩包"""
        if self.db is None:
            self._init_db()
            
        target = "Msg_" + _md5_hex(room_id.encode())
        min_ctime = 0
        if days_limit:
            min_ctime = int(time.time()) - (days_limit * 86400)
            
        all_records = []
        for rel in self.db._message_dbs():
            try:
                conn = self.db._open(rel)
                cur = conn.cursor()
                query = f"""
                    SELECT local_id, local_type, real_sender_id, create_time, message_content
                    FROM {target} WHERE create_time >= ? ORDER BY sort_seq ASC
                """
                cur.execute(query, (min_ctime,))
                is_private = not room_id.endswith("@chatroom")
                for r in cur.fetchall():
                    lid, ltype, sid, ctime, mcontent = r
                    body = parse_real_content(mcontent)
                    if not body or "拍了拍" in body:
                        continue
                    if is_private:
                        if sid == 2 or sid == 0 or sid == 15:
                            sender_nick = "我"
                        else:
                            sender_nick = room_name
                    else:
                        sender_nick = self.get_cached_nickname(sid, db_rel=rel) or str(sid)
                    all_records.append({
                        "id": lid,
                        "time": ctime,
                        "datetime": datetime.datetime.fromtimestamp(ctime).strftime("%Y-%m-%d %H:%M:%S"),
                        "date": datetime.datetime.fromtimestamp(ctime).strftime("%Y-%m-%d"),
                        "sender": sender_nick,
                        "content": body
                    })
                conn.close()
            except Exception:
                pass
                
        all_records.sort(key=lambda x: x["time"])
        
        # 创建知识库目录
        clean_name = re.sub(r'[\/\\:\*\?"<>\|]', '_', room_name)
        kb_folder_name = f"{clean_name}_AI知识库"
        kb_path = os.path.join(self.data_dir, kb_folder_name)
        os.makedirs(os.path.join(kb_path, "00_技术专题与避坑指南"), exist_ok=True)
        os.makedirs(os.path.join(kb_path, "01_原始群聊对话归档"), exist_ok=True)
        os.makedirs(os.path.join(kb_path, "02_结构化数据_JSONL"), exist_ok=True)
        
        # 1. 导出完整原始 Markdown
        md_file = os.path.join(kb_path, "01_原始群聊对话归档", f"{clean_name}_完整对话归档.md")
        with open(md_file, "w", encoding="utf-8") as f:
            f.write(f"# {room_name} 完整历史交流归档\n\n")
            f.write(f"> 记录总数: {len(all_records)} 条 | 导出时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n")
            current_date = ""
            for rec in all_records:
                if rec["date"] != current_date:
                    current_date = rec["date"]
                    f.write(f"\n## 📅 {current_date}\n\n")
                t_str = rec["datetime"][11:16]
                f.write(f"**[{t_str}] {rec['sender']}**:\n{rec['content']}\n\n")
                
        # 2. 导出 JSONL
        jsonl_file = os.path.join(kb_path, "02_结构化数据_JSONL", f"{clean_name}.jsonl")
        with open(jsonl_file, "w", encoding="utf-8") as f:
            for rec in all_records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                
        # 3. 自动生成 Agent_Prompt.md
        prompt_file = os.path.join(kb_path, "Agent_Prompt.md")
        with open(prompt_file, "w", encoding="utf-8") as f:
            if not room_id.endswith("@chatroom"):
                f.write(f"""# 与「{room_name}」专属私聊往来记忆与高情商智囊系统引导词

你是由我与「{room_name}」的全部历史私聊记录（共 {len(all_records)} 条往来对话）训练赋能的私人专属军师与高情商智囊。

## 核心职责与原则
1. **深度掌握往来背景**：熟悉我与「{room_name}」曾沟通的事项、关键话题、约定与合作进展；
2. **洞悉对方决策习惯**：理解「{room_name}」的沟通偏好、性格特点与核心关切，避免触碰沟通雷区；
3. **高情商与得体回复**：给出回复建议时，语言真诚、自然、拿捏分寸、切中要害，绝不使用死板的客服腔与机械八股文；
4. **备忘与未决事项追踪**：适时提醒对话中曾提及的待办事项与未决议题。
""")
            else:
                f.write(f"""# {room_name} 专属群军师系统引导词

你是由「{room_name}」历史沉淀交流经验（共 {len(all_records)} 条实测讨论）训练赋能的资深技术顾问。

## 核心职责与原则
1. **真实一手经验**：优先基于知识库中群友的复现测试与真实报错给出解答；
2. **时效性判定**：如果前后时间存在方案冲突，以更新月份的群友实测与官方 patch 结论为准；
3. **言简意赅**：在微信群中发言风格要利落、专业、直击痛点，指出常见踩坑点；
4. **客观呈现争议**：对尚无定论的问题，客观列出两派观点，不盲目下结论；
5. **格式规范**：直接输出回复内容，不要带多余客套寒暄。
""")

        # 4. 生成 README.md
        readme_file = os.path.join(kb_path, "README.md")
        with open(readme_file, "w", encoding="utf-8") as f:
            f.write(f"""# {room_name} · AI 学术与技术交流知识库

- **来源群聊**: {room_name} (`{room_id}`)
- **收录规模**: {len(all_records)} 条清洗对话
- **生成时间**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 知识库目录规范
- `Agent_Prompt.md`: 专为任意大模型 Agent 配置的系统角色设定与推理准则
- `00_技术专题与避坑指南/`: 提炼的核心技术专题与高频常见问题解答
- `01_原始群聊对话归档/`: 按时间排序的清晰 Markdown 对话流
- `02_结构化数据_JSONL/`: 供向量数据库与 RAG 引擎一键切片的标准 JSON 数据集
""")

        # 4.5 保存知识库元数据 (用于增量同步与快速加载)
        meta_info = {
            "type": "single",
            "room_id": room_id,
            "room_name": room_name,
            "clean_name": clean_name,
            "created_at": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "last_msg_time": max([r.get("time", 0) for r in all_records]) if all_records else int(time.time()),
            "total_messages": len(all_records),
            "kb_structure": "standard",
            "kb_title": room_name
        }
        with open(os.path.join(kb_path, ".kb_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta_info, f, ensure_ascii=False, indent=2)

        # 5. 打包为 zip
        zip_filename = f"{clean_name}_AI知识库.zip"
        zip_filepath = os.path.join(self.data_dir, zip_filename)
        with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(kb_path):
                for file in files:
                    full_p = os.path.join(root, file)
                    rel_p = os.path.relpath(full_p, kb_path)
                    zf.write(full_p, rel_p)

        return {
            "status": "success",
            "total_exported": len(all_records),
            "folder_name": kb_folder_name,
            "zip_filename": zip_filename,
            "zip_path": zip_filepath,
            "download_url": f"/api/download_kb/{zip_filename}"
        }

    def _call_llm_for_distillation(self, chunk_text: str) -> str:
        """【阶段1：高浓度微观事实萃取】并行分析切片，提取技术事实、人物言行与硬件行情"""
        api_url = self.llm_config.get("api_url", "").strip()
        api_key = self.llm_config.get("api_key", "").strip()
        model = self.llm_config.get("model", "qwen27b").strip()
        if not api_url:
            return ""

        proxies = {"http": None, "https": None} if is_private_ip(api_url) else None
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        system_prompt = (
            "你是一个顶级社群情报分析师与资深大模型系统架构师。\n"
            "以下是一段从技术群/行业群中截取的真实连续聊天记录切片。\n"
            "请以最细致、不遗漏任何有价值细节的标准，对本切片进行高纯度事实萃取：\n\n"
            "【萃取三大核心维度】：\n"
            "1. 🛠️ [技术实测与踩坑]：真实发生的问题报错、环境参数（GPU/驱动/CUDA/量化方式/参数配置/启动命令）及实测验证的有效解决手段或避坑经验；\n"
            "2. 👤 [人物画像与言行]：谁发表了独特的观点？谁表现出特定领域的专长或硬件装备？谁近期购买/转让了什么？谁表现出独特的沟通性格？\n"
            "3. 💰 [硬件行情与设备交易]：提及的显卡、主板、准系统、配件、实测功耗、发热、二手收售价、渠道评价或避坑黑名单；\n"
            "4. 🔤 [圈内黑话/缩写梗]：涉及的群内特有黑话、缩写、俗称或谐音梗。\n\n"
            "【输出规则】：\n"
            "- 如果切片纯属无营养寒暄、灌水打屁且毫无上述四类信息，请务必直接输出：【PASS】；\n"
            "- 若有高价值信息，请分点清晰列出，务必使用地道严谨的中文，保留发言人昵称与关键数字/命令。"
        )
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"请对以下群聊交流切片进行深度事实萃取：\n\n{chunk_text}"}
            ],
            "temperature": 0.3,
            "max_tokens": 1536
        }
        try:
            target_url = api_url.rstrip("/") + "/chat/completions"
            resp = requests.post(target_url, headers=headers, json=payload, timeout=45, proxies=proxies)
            if resp.status_code == 200:
                data = resp.json()
                msg = data["choices"][0]["message"]
                content = msg.get("content") or ""
                if "<think>" in content and "</think>" in content:
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
                res_clean = content.strip()
                if "【PASS】" in res_clean or res_clean == "PASS":
                    return ""
                return res_clean
        except Exception:
            pass
        return ""

    def _call_llm_synthesis(self, system_role: str, task_prompt: str, context_facts: str) -> str:
        """【阶段2：三路专家全局交叉重构】调用各领域顶级专家提示词进行宏观维基编写"""
        api_url = self.llm_config.get("api_url", "").strip()
        api_key = self.llm_config.get("api_key", "").strip()
        model = self.llm_config.get("model", "qwen27b").strip()
        if not api_url:
            return ""

        proxies = {"http": None, "https": None} if is_private_ip(api_url) else None
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_role},
                {"role": "user", "content": f"{task_prompt}\n\n【全量事实素材库】：\n{context_facts}"}
            ],
            "temperature": 0.4,
            "max_tokens": 4096
        }
        try:
            target_url = api_url.rstrip("/") + "/chat/completions"
            resp = requests.post(target_url, headers=headers, json=payload, timeout=120, proxies=proxies)
            if resp.status_code == 200:
                data = resp.json()
                msg = data["choices"][0]["message"]
                content = msg.get("content") or ""
                if "<think>" in content and "</think>" in content:
                    content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
                return content.strip()
        except Exception:
            pass
        return ""

    def export_merged_knowledge_base(
        self,
        sessions: List[dict],
        kb_title: str,
        days_limit: Optional[int] = None,
        deep_distill: bool = False
    ) -> dict:
        """合并多个群聊/私聊记录，支持时间切片与大模型深度 Q&A 蒸馏提炼"""
        if self.db is None:
            self._init_db()

        clean_title = re.sub(r'[\\/:*?"<>|]', '_', kb_title.strip()) or "综合技术交流知识库"
        min_ctime = 0
        if days_limit:
            min_ctime = int(time.time()) - (days_limit * 86400)

        all_records = []
        source_names = []

        total_sessions = len(sessions)
        for s_idx, s in enumerate(sessions, 1):
            rid = s.get("room_id") or s.get("id")
            rname = s.get("room_name") or s.get("name") or rid
            if not rid:
                continue
            source_names.append(rname)
            
            self.distillation_progress["is_running"] = True
            self.distillation_progress["status"] = "reading"
            self.distillation_progress["percent"] = int((s_idx / (total_sessions + 1)) * 25)
            self.distillation_progress["message"] = f"正在极速读取会话 [{rname}] ({s_idx}/{total_sessions})..."
            
            sub_records = self.fetch_room_messages(rid, rname, min_ctime)
            for item in sub_records:
                all_records.append({
                    "session_id": rid,
                    "source_name": rname,
                    "timestamp": item.get("time") or item.get("timestamp") or 0,
                    "time_str": item.get("datetime") or "",
                    "sender": item.get("sender") or "",
                    "content": item.get("content") or ""
                })

        # 按毫秒时间戳进行全局排序
        all_records.sort(key=lambda x: x["timestamp"])

        # 创建输出目录结构
        kb_folder_name = f"{clean_title}_AI知识库"
        kb_path = os.path.join(self.data_dir, kb_folder_name)
        os.makedirs(kb_path, exist_ok=True)
        os.makedirs(os.path.join(kb_path, "00_技术专题与避坑指南"), exist_ok=True)
        os.makedirs(os.path.join(kb_path, "01_原始群聊对话归档"), exist_ok=True)
        os.makedirs(os.path.join(kb_path, "02_结构化数据_JSONL"), exist_ok=True)

        # 1. 原始对话流水账 Markdown
        raw_md_path = os.path.join(kb_path, "01_原始群聊对话归档", "多群合并全量对话归档.md")
        with open(raw_md_path, "w", encoding="utf-8") as f:
            f.write(f"# {clean_title} · 多群合并全量对话归档\n\n")
            f.write(f"- **合并来源**: {', '.join(source_names)}\n")
            f.write(f"- **总对话条数**: {len(all_records)} 条\n")
            f.write(f"- **归并生成时间**: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n---\n\n")
            for item in all_records:
                f.write(f"**[{item['source_name']}] [{item['time_str']}] {item['sender']}**:\n{item['content']}\n\n")

        # 2. 结构化 JSONL 数据
        jsonl_path = os.path.join(kb_path, "02_结构化数据_JSONL", "多群合并数据集.jsonl")
        with open(jsonl_path, "w", encoding="utf-8") as f:
            for item in all_records:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")

        # 3. 智能轻量预清洗与高纯度话题切片
        distilled_facts_list = []
        tech_manual_content = ""
        profiles_content = ""
        slang_content = ""
        hardware_content = ""

        if deep_distill and len(all_records) > 0:
            # 规则前置轻量级脱噪（极速剔除纯灌水、表情包及无实体短对话）
            noise_words = {"好的", "收到", "ok", "666", "哈哈", "哈哈哈", "嗯嗯", "是的", "对", "mark", "已收到", "点赞", "强", "牛", "感谢"}
            tech_keywords = [
                "error", "fail", "cuda", "vllm", "sglang", "ollama", "awq", "gptq", "fp8", "int4",
                "显存", "显卡", "4090", "3090", "h100", "a100", "v100", "p40", "t4", "rtx",
                "nvlink", "pcie", "tp", "pp", "量化", "微调", "吞吐", "并发", "准系统", "电源",
                "主板", "散热", "风扇", "水冷", "驱动", "内核", "ubuntu", "docker", "参数", "报错",
                "多少钱", "出", "收", "买", "卖", "老哥", "大佬", "怎么", "为什么", "求", "有人"
            ]

            def is_valuable_message(msg_text: str) -> bool:
                m_clean = msg_text.strip()
                if len(m_clean) < 4:
                    return False
                if m_clean in noise_words:
                    return False
                if m_clean.startswith("[") and m_clean.endswith("]") and len(m_clean) < 15:
                    return False
                if len(m_clean) >= 15:
                    return True
                m_lower = m_clean.lower()
                return any(k in m_lower for k in tech_keywords)

            cleaned_records = [r for r in all_records if is_valuable_message(r.get("content", ""))]
            if not cleaned_records:
                cleaned_records = all_records  # 兜底防止过度过滤

            # 按 15 分钟闲置或达到 20 条消息进行完整无死角切片 (Chunking)
            chunks = []
            curr_chunk = []
            last_ts = 0
            for item in cleaned_records:
                if curr_chunk and (item["timestamp"] - last_ts > 900 or len(curr_chunk) >= 20):
                    chunks.append(curr_chunk)
                    curr_chunk = []
                curr_chunk.append(item)
                last_ts = item["timestamp"]
            if curr_chunk:
                chunks.append(curr_chunk)

            total_chunks = len(chunks)
            if total_chunks > 0:
                self.distillation_progress["total"] = total_chunks
                self.distillation_progress["is_running"] = True
                self.distillation_progress["status"] = "distilling"

                # === 阶段 1：全量微观脱噪与事实萃取 (多线程并行，100%全覆盖) ===
                extracted_facts = []
                completed_count = 0
                max_workers = 6

                def process_chunk(idx, c):
                    chunk_text = "\n".join([f"[{m['source_name']}] {m['sender']}: {m['content']}" for m in c])
                    fact = self._call_llm_for_distillation(chunk_text)
                    return idx, fact

                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = {executor.submit(process_chunk, idx, c): idx for idx, c in enumerate(chunks)}
                    for future in as_completed(futures):
                        completed_count += 1
                        idx, fact = future.result()
                        if fact and len(fact) > 20:
                            extracted_facts.append(fact)
                        p_val = int(25 + (completed_count / total_chunks) * 45)  # 25% -> 70%
                        self.distillation_progress["current"] = completed_count
                        self.distillation_progress["percent"] = p_val
                        self.distillation_progress["message"] = f"【阶段1/2 全量穿透】已萃取 {completed_count}/{total_chunks} 个话题切片 (沉淀高纯度事实 {len(extracted_facts)} 组)..."

                distilled_facts_list = extracted_facts

                # === 阶段 2：三路专家交叉重构维基 (Thematic Reduce 阶段) ===
                self.distillation_progress["percent"] = 75
                self.distillation_progress["message"] = "【阶段2/2 专家三路重构】架构师、社群专家、硬件买手正在交叉编译维基..."

                # 聚合全部微观事实为全局上下文
                all_facts_text = "\n\n---\n\n".join(distilled_facts_list) if distilled_facts_list else "（未提取到事实）"

                # 专家 1：大模型系统与推理架构师
                prompt_tech_role = "你是一名享誉业界的顶级大模型系统与推理架构师，精通 vLLM/SGLang 源码、GPU通信拓扑及高并发生产部署。"
                prompt_tech_task = (
                    f"请基于以下从群聊事实库中提取的全部干货，为「{clean_title}」撰写一份权威硬核的《核心技术专题与实测避坑手册》。\n"
                    "要求全面结构化，必须包含四大篇章：\n"
                    "1. 🚀 GPU算力拓扑与多卡通信篇（PCIe带宽瓶颈、NVLink互联实测、NUMA节点绑定）；\n"
                    "2. ⚡ 推理引擎压榨与量化部署篇（vLLM、SGLang、Ollama实战部署参数，AWQ/FP8量化取舍）；\n"
                    "3. 🛠️ 群友实战高频报错与精准排错手册（真实报错日志、根因、验证有效的修复命令）；\n"
                    "4. ⚠️ 生产落地避坑铁律（杜绝机械空话，给出真实数据与参数配置）。"
                )

                # 专家 2：社群社会学与人物关系分析师
                prompt_profile_role = "你是一名资深社群社会学与网络人际关系分析师，擅长从海量对话中刻画人物性格、技术专长及影响力网络。"
                prompt_profile_task = (
                    f"请基于以下事实素材，为「{clean_title}」社群编写两份档案：\n"
                    "【部分一：关键人物人设档案图谱】\n"
                    "挑出发言活跃、技术过硬或具有代表性性格的群友/KOL，为每个人建立档案卡：\n"
                    "- 👤 昵称：...\n"
                    "- 🏷️ 社群生态定位：(如：硬件极客、底层算力老兵、收卡倒爷、严谨学者、萌新提问者等)\n"
                    "- 💡 专长技术领域：(如：vLLM调参、二手服务器改水冷、多卡通信调优)\n"
                    "- 🖥️ 主力装备/实操经验：(如：拥有8卡4090机架、折腾过浪潮准系统)\n"
                    "- 🗣️ 发言风格与性格：(如：直爽不废话、喜欢甩命令、报喜不报忧)\n\n"
                    "【部分二：圈内特有黑话与隐语缩写词典】\n"
                    "整理群内出现过的所有黑话、设备俗称、缩写梗与行业暗号，给出地道解释。"
                )

                # 专家 3：资深硬件供应链买手与行情分析师
                prompt_hw_role = "你是一名在华强北和二手服务器机房摸爬滚打十余年的资深硬件供应链老买手与行情分析师。"
                prompt_hw_task = (
                    f"请基于以下事实素材，为「{clean_title}」整理一份《真实硬件行情晴雨表与选型避坑指南》：\n"
                    "1. 💰 核心硬件二手/渠道真实收售价盘点（包括各类显卡、准系统、CPU、服务器主板在群内提及的真实成交/求购价）；\n"
                    "2. ⚖️ 硬件性价比与实测表现梯队榜（针对大模型私有化部署的最佳准系统方案与显卡组合）；\n"
                    "3. 🚫 翻车避坑黑名单（发热暴雷、兼容性极差、虚标电源、暗病众多的硬件型号与避坑建议）。"
                )

                # 并发执行三位专家的编译
                with ThreadPoolExecutor(max_workers=3) as synth_pool:
                    future_tech = synth_pool.submit(self._call_llm_synthesis, prompt_tech_role, prompt_tech_task, all_facts_text)
                    future_prof = synth_pool.submit(self._call_llm_synthesis, prompt_profile_role, prompt_profile_task, all_facts_text)
                    future_hw = synth_pool.submit(self._call_llm_synthesis, prompt_hw_role, prompt_hw_task, all_facts_text)

                    tech_manual_content = future_tech.result()
                    both_prof_content = future_prof.result()
                    hardware_content = future_hw.result()

                # 拆分人物档案与黑话词典（若大模型合在一个回复中）
                profiles_content = both_prof_content
                slang_content = "详见《02_关键人物档案与人设图谱.md》中的词典章节。"

            self.distillation_progress["status"] = "completed"
            self.distillation_progress["is_running"] = False
            self.distillation_progress["percent"] = 100
            self.distillation_progress["message"] = "五维立体知识库提炼构建全部完成！"

        # === 阶段 3：全景维度文档落盘与灵魂注入 ===
        # 1. 创建五维立体知识库专卷
        d5_dir = os.path.join(kb_path, "00_五维立体知识库")
        os.makedirs(d5_dir, exist_ok=True)

        # 卷一：核心技术专题与实测避坑手册
        manual_path = os.path.join(d5_dir, "01_核心技术专题与实测避坑手册.md")
        with open(manual_path, "w", encoding="utf-8") as f:
            if tech_manual_content:
                f.write(tech_manual_content)
            else:
                f.write(f"# {clean_title} · 核心技术专题与实测避坑手册\n\n（未启用全量深度蒸馏，或当前群聊未提取到有效技术内容）\n")

        # 卷二：关键人物档案与人设图谱
        prof_path = os.path.join(d5_dir, "02_关键人物档案与人设图谱.md")
        with open(prof_path, "w", encoding="utf-8") as f:
            if profiles_content:
                f.write(profiles_content)
            else:
                f.write(f"# {clean_title} · 关键人物档案与人设图谱\n\n（未启用全量深度蒸馏）\n")

        # 卷三：真实硬件行情与选型避坑晴雨表
        hw_path = os.path.join(d5_dir, "03_真实硬件行情与选型避坑晴雨表.md")
        with open(hw_path, "w", encoding="utf-8") as f:
            if hardware_content:
                f.write(hardware_content)
            else:
                f.write(f"# {clean_title} · 真实硬件行情与选型避坑晴雨表\n\n（未启用全量深度蒸馏）\n")

        # 卷四：圈内特有黑话与隐语缩写词典
        slang_path = os.path.join(d5_dir, "04_圈内黑话与隐语暗号词典.md")
        with open(slang_path, "w", encoding="utf-8") as f:
            f.write(f"# {clean_title} · 圈内特有黑话与隐语暗号词典\n\n" + (profiles_content if profiles_content else "（未启用全量深度蒸馏）\n"))

        # 同步兼容历史路径：00_技术专题与避坑指南/01_多群聚合实测经验与避坑指南.md
        qa_doc_path = os.path.join(kb_path, "00_技术专题与避坑指南", "01_多群聚合实测经验与避坑指南.md")
        with open(qa_doc_path, "w", encoding="utf-8") as f:
            if tech_manual_content:
                f.write(tech_manual_content)
            elif distilled_facts_list:
                f.write(f"# {clean_title} · 核心技术专题与实测避坑手册\n\n")
                for idx, qa in enumerate(distilled_facts_list, 1):
                    f.write(f"## 专题实录 {idx}\n{qa}\n\n---\n\n")
            else:
                f.write("（未启用大模型深度蒸馏，或当前切片中未提取到结构化技术问答）\n")

        # 4. 生成注入全景世界观的 Agent_Prompt.md
        prompt_path = os.path.join(kb_path, "Agent_Prompt.md")
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(f"""# {clean_title} · 顶级 AI 军师系统全景认知引导词

你是由「{clean_title}」（涵盖：{', '.join(source_names)}）数万条真实交流沉淀训练而成的顶级专家军师。
你不仅熟知底层所有技术细节与报错避坑经验，还彻底通晓群内老玩家的人设背景、说话行话风格以及硬件交易二手底价。

## 核心维度掌握：
1. **技术拓扑与排错**：精通知识库《01_核心技术专题与实测避坑手册》中记录的实测参数、报错命令及量化配置；
2. **社群人物洞察**：熟知知识库《02_关键人物档案与人设图谱》中各位核心领袖与常客的技术长板、性格习惯及潜台词；
3. **硬件真实行情**：通晓知识库《03_真实硬件行情与选型避坑晴雨表》中的一手二手价格、渠道评价与翻车雷区。

## 军师回复铁律：
- 🎯 **直击要害**：给出推荐回复草稿时，必须符合群内硬核老鸟的语气，简洁、沉稳、专业；
- 👤 **知己知彼**：遇特定人物提问时，结合其历史发言与性格标签进行针对性拆解；
- 💰 **行情敏锐**：涉及询价、买卖、硬件选型时，用真实底价和群友实测教训提供权威参考。
""")

        # 4.5 保存合并知识库元数据
        meta_info = {
            "type": "merge",
            "sessions": sessions,
            "source_names": source_names,
            "clean_title": clean_title,
            "created_at": datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            "last_msg_time": max([r.get("timestamp", 0) for r in all_records]) if all_records else int(time.time()),
            "total_messages": len(all_records),
            "distilled_count": len(distilled_facts_list),
            "kb_structure": "5D",
            "kb_title": clean_title
        }
        with open(os.path.join(kb_path, ".kb_meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta_info, f, ensure_ascii=False, indent=2)

        # 5. 打包 Zip
        zip_filename = f"{clean_title}_AI综合知识库.zip"
        zip_filepath = os.path.join(self.data_dir, zip_filename)
        with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(kb_path):
                for file in files:
                    full_p = os.path.join(root, file)
                    rel_p = os.path.relpath(full_p, kb_path)
                    zf.write(full_p, rel_p)

        res_dict = {
            "status": "success",
            "total_exported": len(all_records),
            "distilled_count": len(distilled_facts_list),
            "folder_name": kb_folder_name,
            "zip_filename": zip_filename,
            "zip_path": zip_filepath,
            "download_url": f"/api/download_kb/{zip_filename}"
        }
        self.distillation_progress["result"] = res_dict
        return res_dict

    def analyze_intent(self, text: str, session_type: str = "group") -> Tuple[bool, str]:
        """分析消息意图：私聊一律触发高情商建议；群聊过滤纯表情与水聊"""
        t = text.strip()
        if not t:
            return False, "无实质内容"
        # 纯系统通知过滤
        if any(ignore in t for ignore in ["拍了拍", "加入了群聊", "移出了群聊", "撤回了一条消息"]):
            return False, "系统通知"

        # 1. 好友私聊（1对1）：只要发了文字消息，一律必须生成高情商回复建议！
        if session_type == "private":
            # 只过滤单字符纯标点
            if len(t) == 1 and not ('\u4e00' <= t <= '\u9fa5' or t.isalnum()):
                return False, "单标点符号"
            return True, "好友私聊来信"

        # 2. 群聊防灌水过滤
        if len(t) < 2:
            return False, "无实质内容"
        pure_emojis = re.sub(r"\[[a-zA-Z0-9\u4e00-\u9fa5]+\]", "", t).strip()
        if not pure_emojis and len(t) <= 12:
            return False, "纯表情互动"
        if t in ["收到", "好的", "好的收到", "ok", "OK", "666", "厉害", "牛逼", "哈哈", "哈哈哈", "确实", "赞"]:
            return False, "简短附和"
            
        # 判断意图标签
        if any(w in t for w in ["?", "？", "怎么", "如何", "为啥", "为什么", "请问", "求助", "能否", "有没有", "卡在", "报错", "启动不了", "在吗", "在干嘛", "干嘛呢"]):
            return True, "提问/交流"
        if any(w in t for w in ["vllm", "cuda", "v100", "4090", "3090", "qwen", "deepseek", "显存", "吞吐", "量化", "fp8", "awq", "bc", "中间件"]):
            return True, "核心技术探讨"
        if any(w in t for w in ["价格", "降价", "算力", "成本", "便宜", "贵", "租用", "开销", "分钱", "1毛", "token"]):
            return True, "算力商业/成本讨论"
        if len(t) >= 4:
            return True, "群聊交流/观点发表"
        return False, "日常简短寒暄"

    def get_sender_history(self, sender: str, max_msgs: int = 6) -> str:
        """从知识库中提取该发言人的历史发言与被提及记录，还原其真实画像与近期动向"""
        if not sender or sender in ("群友", "我", "系统消息"):
            return ""
        history = []
        target = sender.lower()
        import glob
        for jf in glob.glob(os.path.join(self.data_dir, "**", "02_结构化数据_JSONL", "*.jsonl"), recursive=True):
            try:
                with open(jf, "r", encoding="utf-8", errors="ignore") as f:
                    for line in f:
                        if target in line.lower():
                            obj = json.loads(line)
                            s = str(obj.get("sender", "")).lower()
                            c = str(obj.get("content", ""))
                            t = str(obj.get("time_str", ""))
                            if target == s or c.lower().startswith(target + ":"):
                                clean_c = re.sub(r"^[^:]+:\s*", "", c).strip()
                                if len(clean_c) > 2 and "拍了拍" not in clean_c and "<msg>" not in clean_c:
                                    history.append(f"[{t}] {clean_c}")
            except Exception:
                pass
        if len(history) > max_msgs:
            history = history[-max_msgs:]
        return "\n".join(history)

    def call_llm_advice(self, question: str, sender: str = "群友", session_name: str = "", session_type: str = "group", force_creative: bool = False) -> str:
        """调用用户配置的大模型生成专家建议：结合本地知识库高权置信度 + 联网搜索补充"""
        api_url = self.llm_config.get("api_url", "").strip()
        api_key = self.llm_config.get("api_key", "").strip()
        model = self.llm_config.get("model", "deepseek-chat").strip()
        
        # 1. 本地知识库高权重智能检索 (自动检索当前会话库与综合知识库)
        kb_info = ""
        if os.path.exists(self.data_dir):
            for d in os.listdir(self.data_dir):
                full_d = os.path.join(self.data_dir, d)
                if os.path.isdir(full_d) and (d.endswith("_AI知识库") or "知识库" in d):
                    kb_part = search_local_kb(question, full_d, max_chars=800)
                    if kb_part:
                        kb_info += f"\n{kb_part}\n"
                        if len(kb_info) >= 1200:
                            break
        
        # 2. 深入知识库提取该发言人真实画像与历史言行轨迹
        sender_history = self.get_sender_history(sender, max_msgs=6)

        # 3. 联网搜索兜底补充
        web_info = ""
        if len(kb_info) < 80:
            web_info = search_bing(question, max_results=2)
            
        system_prompt = (
            "你是隐身于微信背后的顶级“超级个人军师”与资深技术战略架构师。\n"
            "你的使命是：让聊天记录如同血肉一样融入你的认知，无所不知，洞悉群内生态、懂人情世故、精通专业知识，为软件使用者提供降维打击级的超凡辅助。\n\n"
            "请严格按照以下【4大核心模块】组织卡片输出内容（层次清晰，干货拉满，严禁任何AI客服八股文与多余客套）：\n\n"
            "🎯【推荐回复草稿】\n"
            "（核心直接先给答案！深度模仿群内真实资深老玩家与高手的沟通风格：自然、内行、不做作、懂人情世故，可直接按 Ctrl+V 发送）\n\n"
            "👤【发言人画像与群内定位】\n"
            "（立体还原：他是个什么样的人？在群里处于什么角色生态位（如技术老兵/小白求助/倒买倒卖/折腾党/潜水员）？最近在折腾什么具体硬件、卡型或项目？）\n\n"
            "🔍【意图剖析与潜台词拆解】\n"
            "（结合该角色画像与本次发言深度透视：他表面这句话背后在想什么？潜台词是什么？真实意图与目的是什么（技术摸底/试探底价/求助/吹水/情绪发泄）？）\n\n"
            "🧠【专家推演与思考过程】\n"
            "（结合知识库里的历史上下文、真实踩坑血泪史与技术原理，为使用者呈现完整的专家决策推演逻辑与避坑参考指南。）"
        )
        
        user_prompt = f"【会话来源】：{session_name or ('微信好友私聊' if session_type == 'private' else '微信技术群聊')}\n"
        user_prompt += f"【当前发言人】：{sender}\n"
        user_prompt += f"【当前发言内容】：\n“{question}”\n"
        
        if sender_history:
            user_prompt += f"\n【发言人「{sender}」的历史发言轨迹与近期动向】：\n{sender_history}\n"
        if kb_info:
            user_prompt += f"\n【群内过往沉淀知识库与讨论记录】：\n{kb_info}\n"
        if web_info:
            user_prompt += f"\n【全网最新情报补充参考】：\n{web_info}\n"
            
        user_prompt += "\n请按照 4 大核心模块输出你的超级军师研判卡片："
        
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        proxies = {"http": None, "https": None} if is_private_ip(api_url) else None
        
        if api_url:
            try:
                url = api_url.rstrip("/") + "/chat/completions"
                temp = float(self.llm_config.get("temperature", 0.7))
                if force_creative:
                    temp = min(1.0, temp + 0.25)
                payload = {
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    "temperature": temp,
                    "max_tokens": 1200
                }
                resp = requests.post(url, headers=headers, json=payload, timeout=25, proxies=proxies)
                if resp.status_code == 200:
                    data = resp.json()
                    msg = data["choices"][0]["message"]
                    content = (msg.get("content") or "").strip()
                    reasoning = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
                    
                    ans = ""
                    # 优先提取 content
                    if content:
                        ans = content
                    elif reasoning:
                        # 如果 content 为空只有 reasoning，且为中文，尝试提取中文文本，绝不能返回英文思维链！
                        ch_chars = re.findall(r"[一-龥]", reasoning)
                        if len(ch_chars) >= 15:
                            quotes = re.findall(r'["\u201c\u201d\u300c\u300d]([^"\u201c\u201d\u300c\u300d]+)["\u201c\u201d\u300c\u300d]', reasoning)
                            val_quotes = [q for q in quotes if len(re.findall(r"[\u4e00-\u9fa5]", q)) >= 6]
                            if val_quotes:
                                ans = val_quotes[-1]
                            else:
                                ans = reasoning
                                
                    if ans:
                        clean_ans = ans.strip().strip("“”\"'")
                        # 过滤纯英文漏网之鱼
                        if len(re.findall(r"[一-龥]", clean_ans)) >= 2:
                            return clean_ans
            except Exception as e:
                print(f"[LLM调用异常, 进入知识库兜底]: {e}")
                
        # 知识库与情商启发式兜底
        if session_type == "private":
            q = question.lower()
            if "生日" in q:
                return f"生日快乐呀{session_name or sender}！🎂 今天你最大，想要什么安排，必须狠狠开心庆祝一下！"
            if "干嘛" in q or "在吗" in q or "忙吗" in q:
                return "在呢，刚忙完手头的事，怎么啦找我有事呀？"
            if "吃了吗" in q or "吃饭" in q:
                return "刚吃完呢，你吃过了没？"
            return f"收到！可以直接回复：“好的，我了解了，我稍后仔细看下回复你哈。”"
            
        if kb_info:
            return f"从之前群里的排坑经验来看：{kb_info[:150]}... 建议重点沿着这个方向排查一下环境与参数。"
        q = question.lower()
        if "启动" in q or "输入什么" in q or "speedtest" in q:
            return "如果是测速页面，API地址填 http://localhost:8000/v1（云端填公网IP:8000/v1，放行8000端口）；Key填EMPTY；模型填启动时的名字，填好点开始测试即可。"
        if "降价" in q or "梁圣" in q or "缓存" in q or "命中" in q or "plan" in q or "分钱" in q:
            return "DeepSeek 现在的 Context Caching 确实很香，命中直接变5分钱/M，非命中或高峰期大概1毛。长前缀场景把固定上下文锁住，省钱效果极其明显。"
        if "nccl" in q or "卡死" in q or "卡住" in q:
            return "先排查通信拓扑：加环境变量 VLLM_DISABLE_PYNCCL=1 绕过 Python 端 NCCL 初始化；用 nvidia-smi topo -m 查看卡间 P2P 状态。多卡老机器经常在这死锁。"
        if "oom" in q or "显存" in q or "溢出" in q:
            return "建议把 --gpu-memory-utilization 压到 0.85 给动态 KV 留够冗余；同时限制 --max-model-len 上下文。TP 张量并行确保切分为 2 的整次幂。"
        return "建议重点排查一下显卡架构算力支持、CUDA 驱动版本与 PyTorch wheel 的匹配情况，贴一下具体报错堆栈更容易定位。"

    def regenerate_advice(self, content: str, sender: str = "群友", session_name: str = "", session_type: str = "group") -> str:
        """对已生成的回复不满意时，重新调用大模型换一个角度生成"""
        return self.call_llm_advice(content, sender, session_name=session_name, session_type=session_type, force_creative=True)

    def start_monitoring(self, sessions, room_name: str = "", enable_clipboard: bool = True, enable_chime: bool = True) -> dict:
        """启动多会话并发监听 (支持同时监听多个群聊 + 个人好友私聊)"""
        if self.db is None:
            self._init_db()
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
                
        # 兼容旧版单个传参或新版多会话列表
        self.monitored_sessions = {}
        if isinstance(sessions, str):
            sid = sessions
            stype = "group" if sid.endswith("@chatroom") else "private"
            self.monitored_sessions[sid] = {"id": sid, "name": room_name or sid, "type": stype}
        elif isinstance(sessions, list):
            for s in sessions:
                sid = s.get("id") or s.get("room_id")
                sname = s.get("display_name") or s.get("name") or s.get("room_name") or sid
                stype = s.get("type") or ("group" if sid.endswith("@chatroom") else "private")
                if sid:
                    self.monitored_sessions[sid] = {"id": sid, "name": sname, "type": stype}

        self.listener = Listener(self.db, interval=0.8)
        
        # 记录是否为多会话模式：多会话模式下由前端聚焦安全同步剪贴板；单会话模式下若后端直接写入剪切板
        is_multi_session = len(self.monitored_sessions) > 1
        
        def make_callback(session_info):
            def _on_msg(msg: dict, lst):
                content = msg.get("content", "").strip()
                if not content:
                    return
                sender_id = msg.get("sender_id")
                # 个人私聊中，过滤自己发出的消息（sender_id 为 2 或 15）
                if session_info["type"] == "private":
                    if sender_id in (2, 0, 15):
                        return
                    sender_nick = session_info["name"]
                else:
                    sender_nick = self.get_cached_nickname(sender_id) or msg.get("sender_username") or "群友"
                    
                now_str = datetime.datetime.now().strftime("%H:%M:%S")
                is_valuable, intent_desc = self.analyze_intent(content, session_type=session_info["type"])
                
                advice = ""
                if is_valuable:
                    advice = self.call_llm_advice(
                        content, 
                        sender_nick, 
                        session_name=session_info["name"],
                        session_type=session_info["type"]
                    )
                    if enable_chime:
                        try:
                            import winsound
                            winsound.MessageBeep(winsound.MB_ICONASTERISK)
                        except Exception:
                            pass
                    # 如果只有单个会话，后端可直接写入剪贴板；若多会话并发，则由前端聚焦会话安全写入，避免多群乱顶剪贴板
                    if enable_clipboard and advice and not is_multi_session:
                        try:
                            pyperclip.copy(advice)
                        except Exception:
                            pass
                            
                event_data = {
                    "id": msg.get("local_id"),
                    "time": now_str,
                    "session_id": session_info["id"],
                    "session_name": session_info["name"],
                    "session_type": session_info["type"],
                    "session_label": "👥 群聊" if session_info["type"] == "group" else "👤 私聊",
                    "sender": sender_nick,
                    "content": content,
                    "is_question": is_valuable,
                    "intent": intent_desc,
                    "advice": advice
                }
                self.recent_events.append(event_data)
                if len(self.recent_events) > 150:
                    self.recent_events.pop(0)
                    
                for sub in list(self.event_subscribers):
                    try:
                        sub(event_data)
                    except Exception:
                        pass
            return _on_msg
            
        for sid, sinfo in self.monitored_sessions.items():
            self.listener.add_listener(sid, make_callback(sinfo))
            
        self.listener.start()
        
        return {
            "status": "running",
            "sessions_count": len(self.monitored_sessions),
            "sessions": list(self.monitored_sessions.values()),
            "interval": 0.8
        }

    def stop_monitoring(self):
        if self.listener:
            try:
                self.listener.stop()
            except Exception:
                pass
            self.listener = None
        return {"status": "stopped"}

    def get_monitor_status(self) -> dict:
        is_running = bool(self.listener and self.listener._thread and self.listener._thread.is_alive()) if self.listener else False
        return {
            "is_monitoring": is_running,
            "sessions": list(getattr(self, "monitored_sessions", {}).values()),
            "sessions_count": len(getattr(self, "monitored_sessions", {}))
        }

    def list_all_knowledge_bases(self) -> list:
        """扫描本地所有已构建的知识库列表"""
        kbs = []
        if not os.path.exists(self.data_dir):
            return kbs
            
        for item in os.listdir(self.data_dir):
            full_path = os.path.join(self.data_dir, item)
            if not os.path.isdir(full_path) or not item.endswith("_AI知识库"):
                continue
                
            meta_path = os.path.join(full_path, ".kb_meta.json")
            if os.path.exists(meta_path):
                try:
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                    meta["folder_name"] = item
                    kbs.append(meta)
                    continue
                except Exception:
                    pass
                    
            # 兼容老版没有 meta 文件的知识库
            title = item.replace("_AI知识库", "")
            kbs.append({
                "type": "single",
                "folder_name": item,
                "kb_title": title,
                "created_at": datetime.datetime.fromtimestamp(os.path.getmtime(full_path)).strftime('%Y-%m-%d %H:%M:%S'),
                "total_messages": 0
            })
            
        # 按最新创建/更新时间倒序排序
        kbs.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return kbs

    def chat_with_kb(self, query: str, kb_folder_name: str = "") -> dict:
        """专家主动问答：用户主动向指定技术知识库提问"""
        q = query.strip()
        if not q:
            return {"answer": "请输入您想了解的问题内容。"}
            
        kb_dir = os.path.join(self.data_dir, kb_folder_name) if kb_folder_name else ""
        if not kb_dir or not os.path.exists(kb_dir):
            all_kbs = self.list_all_knowledge_bases()
            if all_kbs:
                kb_dir = os.path.join(self.data_dir, all_kbs[0]["folder_name"])
            else:
                kb_dir = ""
                
        kb_info = search_local_kb(q, kb_dir, max_chars=2500) if kb_dir else ""
        if not kb_info:
            # 自动跨其他本地知识库兜底检索
            for kb_item in self.list_all_knowledge_bases():
                other_dir = os.path.join(self.data_dir, kb_item["folder_name"])
                if other_dir != kb_dir:
                    extra = search_local_kb(q, other_dir, max_chars=2000)
                    if extra:
                        kb_info = extra
                        kb_dir = other_dir
                        break
        
        system_prompt = (
            "你是基于微信群聊与私聊真实技术交流构建的 AI 军师架构师。\n"
            "你的任务是直接、专业、针对性地回答用户提出的技术、社群人物或硬件行情问题。\n"
            "准则：\n"
            "1. 严格参考【本地知识库参考内容】中记录的实测参数、报错排查、人物特征与硬件行情价格；\n"
            "2. 保持技术老兵的直接、硬核、条理清晰风格，列出明确数据、对比与操作建议；\n"
            "3. 如果知识库中没有明确答案，请运用你的专业知识推导，并诚实说明为专业推测；\n"
            "4. 输出纯中文，杜绝废话和客套开场白。"
        )
        
        user_prompt = f"【本地知识库参考内容】:\n{kb_info if kb_info else '（当前暂未检索到直接相关的群聊原记录）'}\n\n【用户问题】:\n{q}\n\n请给出专业深入的解答："
        
        answer = self._query_llm_direct(system_prompt, user_prompt)
        return {
            "query": q,
            "answer": answer,
            "has_kb_reference": bool(kb_info),
            "kb_folder": os.path.basename(kb_dir) if kb_dir else ""
        }

    def _query_llm_direct(self, system_prompt: str, user_prompt: str, max_tokens: int = 1500) -> str:
        api_url = self.llm_config.get("api_url", "").strip()
        api_key = self.llm_config.get("api_key", "").strip()
        model = self.llm_config.get("model", "deepseek-chat").strip()
        if not api_url:
            return "未配置大模型接口地址，请在 Step 1 中配置并保存模型参数。"
        
        proxies = {"http": None, "https": None} if is_private_ip(api_url) else None
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        try:
            url = api_url.rstrip("/") + "/chat/completions"
            payload = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.6,
                "max_tokens": max_tokens
            }
            resp = requests.post(url, headers=headers, json=payload, timeout=35, proxies=proxies)
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    msg = choices[0].get("message", {})
                    content = (msg.get("content") or "").strip()
                    reasoning = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
                    if content:
                        return content
                    if reasoning:
                        return reasoning
            return f"大模型响应异常 (HTTP {resp.status_code}): {resp.text[:200]}"
        except Exception as e:
            return f"请求大模型服务异常: {e}"

    def incremental_sync_kb(self, kb_folder_name: str) -> dict:
        """知识库一键增量更新：仅拉取上次构建之后的新增记录"""
        kb_path = os.path.join(self.data_dir, kb_folder_name)
        if not os.path.exists(kb_path):
            return {"status": "error", "message": f"知识库目录不存在: {kb_folder_name}"}
            
        meta_path = os.path.join(kb_path, ".kb_meta.json")
        if not os.path.exists(meta_path):
            return {"status": "error", "message": "该知识库缺少元数据信息，请先进行一次完整提炼导出"}
            
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
            
        last_msg_time = meta.get("last_msg_time", 0)
        kb_type = meta.get("type", "single")
        
        new_records = []
        if kb_type == "single":
            room_id = meta.get("room_id")
            room_name = meta.get("room_name") or room_id
            if not room_id:
                return {"status": "error", "message": "缺少 room_id 元数据"}
            new_records = self.fetch_room_messages(room_id, room_name, min_ctime=last_msg_time + 1)
        elif kb_type == "merge":
            sessions = meta.get("sessions", [])
            for sess in sessions:
                sid = sess.get("id") or sess.get("room_id")
                sname = sess.get("name") or sess.get("room_name") or sid
                if sid:
                    new_sub = self.fetch_room_messages(sid, sname, min_ctime=last_msg_time + 1)
                    for r in new_sub:
                        r["_source_name"] = sname
                    new_records.extend(new_sub)
            # 全局时间戳排序
            new_records.sort(key=lambda x: x.get("time", 0))
            
        if not new_records:
            return {
                "status": "up_to_date",
                "new_count": 0,
                "message": "当前知识库已是最新状态，暂无新的聊天记录产生！"
            }
            
        # 1. 追加到原始 Markdown 归档
        archive_dir = os.path.join(kb_path, "01_原始群聊对话归档")
        os.makedirs(archive_dir, exist_ok=True)
        md_files = [f for f in os.listdir(archive_dir) if f.endswith(".md")]
        target_md = os.path.join(archive_dir, md_files[0]) if md_files else os.path.join(archive_dir, "新增对话增量归档.md")
        
        with open(target_md, "a", encoding="utf-8") as f:
            f.write(f"\n\n<!-- 增量同步于 {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} (共 {len(new_records)} 条) -->\n\n")
            for r in new_records:
                src_label = f"[{r.get('_source_name')}] " if "_source_name" in r else ""
                t_val = r.get("datetime") or r.get("time_str") or str(r.get("time", ""))
                s_name = r.get("sender_name") or r.get("sender") or "发言人"
                f.write(f"**[{t_val}] {src_label}{s_name}**:\n{r['content']}\n\n")
                
        # 2. 追加到 JSONL
        jsonl_dir = os.path.join(kb_path, "02_结构化数据_JSONL")
        os.makedirs(jsonl_dir, exist_ok=True)
        jsonl_files = [f for f in os.listdir(jsonl_dir) if f.endswith(".jsonl")]
        target_jsonl = os.path.join(jsonl_dir, jsonl_files[0]) if jsonl_files else os.path.join(jsonl_dir, "incremental.jsonl")
        
        with open(target_jsonl, "a", encoding="utf-8") as f:
            for r in new_records:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                
        # 3. 更新元数据
        new_max_time = max([r.get("time", r.get("timestamp", 0)) for r in new_records])
        meta["last_msg_time"] = new_max_time
        meta["total_messages"] = meta.get("total_messages", 0) + len(new_records)
        meta["last_sync_time"] = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)

        # 3.5 智能增量知识追加（如果有新增技术/人物交流，快速萃取追加）
        if len(new_records) >= 5 and self.llm_config.get("api_url"):
            try:
                inc_text = "\n".join([f"[{r.get('sender_name') or r.get('sender') or '群友'}]: {r.get('content')}" for r in new_records[-30:]])
                patch_fact = self._call_llm_for_distillation(inc_text)
                if patch_fact and "PASS" not in patch_fact and len(patch_fact.strip()) > 30:
                    d5_dir = os.path.join(kb_path, "00_五维立体知识库")
                    if os.path.exists(d5_dir):
                        inc_log_path = os.path.join(d5_dir, "06_近期增量情报补丁.md")
                        with open(inc_log_path, "a", encoding="utf-8") as inc_f:
                            inc_f.write(f"\n\n### 增量情报更新 ({datetime.datetime.now().strftime('%Y-%m-%d %H:%M')})\n{patch_fact}\n")
            except Exception:
                pass
            
        # 4. 重新打包 Zip
        zip_files = [f for f in os.listdir(self.data_dir) if f.startswith(meta.get("clean_name", meta.get("clean_title", ""))) and f.endswith(".zip")]
        if zip_files:
            zip_filepath = os.path.join(self.data_dir, zip_files[0])
            try:
                with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zf:
                    for root, dirs, files in os.walk(kb_path):
                        for file in files:
                            full_p = os.path.join(root, file)
                            rel_p = os.path.relpath(full_p, kb_path)
                            zf.write(full_p, rel_p)
            except Exception:
                pass
                
        return {
            "status": "success",
            "new_count": len(new_records),
            "total_messages": meta["total_messages"],
            "message": f"成功增量同步 {len(new_records)} 条最新聊天记录！"
        }
