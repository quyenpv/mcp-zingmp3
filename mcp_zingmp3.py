# File: mcp_zingmp3.py
# PHIÊN BẢN MỞ RỘNG: Bao gồm Zing MP3 (fix cứng) VÀ YouTube Music
# ĐÃ CHUYỂN TỪ PYTUBE SANG YT-DLP

import mcp.types as types
from mcp.server.fastmcp import FastMCP
import re
import requests
import json
import sys
from typing import List, Dict, Any

# Import thư viện cloudscraper (cho Zing)
import cloudscraper 

# --- THÊM IMPORT CHO YOUTUBE MUSIC (ĐÃ SỬA) ---
try:
    from ytmusicapi import YTMusic
    # from pytube import YouTube # <-- XÓA DÒNG NÀY
    import yt_dlp               # <-- THÊM DÒNG NÀY
except ImportError:
    print("LỖI NGHIÊM TRỌNG: Không tìm thấy thư viện ytmusicapi hoặc yt-dlp.", file=sys.stderr)
    print("Hãy đảm bảo pyproject.toml đã bao gồm 'ytmusicapi' và 'yt-dlp'", file=sys.stderr)
    sys.exit(1)
# --- KẾT THÚC IMPORT MỚI ---

# ===================================================================
# NỘI DUNG TỪ zmp3.py (LOGIC CỦA ZING MP3 - GIỮ NGUYÊN)
# ===================================================================
import time, hashlib, hmac, os
from urllib.parse import quote

URL = "https://zingmp3.vn"

# --- Logic tải cấu hình (ĐÃ FIX CỨNG) ---
try:
    version = "1.16.5"
    akey = "X5BM3w8N7MKozC0B85o4KMlzLZKhV00y"
    skey = "acOrvUS15XRW2o9JksiK1KgQ6Vbds8ZW"
    
    if not all([version, akey, skey]):
        raise ValueError("Giá trị fix cứng bị thiếu")

except Exception as e:
    print(f"LỖI NGHIÊM TRỌNG: Không thể tải cấu hình fix cứng Zing: {e}", file=sys.stderr)
    sys.exit(1)
# --- Kết thúc logic tải cấu hình ---

# --- LOGIC SIGNATURE GỐC (CHO ZING) ---
p = {"ctime", "id", "type", "page", "count", "version"}
# --- KẾT THÚC LOGIC SIG ---

# Khởi tạo session bằng cloudscraper (CHO ZING)
session = cloudscraper.create_scraper() 
_cookie = None

# Khởi tạo YTMusic (CHO YOUTUBE)
try:
    ytmusic = YTMusic()
except Exception as e:
    print(f"LỖI: Không thể khởi tạo YTMusic: {e}", file=sys.stderr)

# utils (CHO ZING)
def hash256(s): return hashlib.sha256(s.encode()).hexdigest()
def hmac512(s, key): return hmac.new(key.encode(), s.encode(), hashlib.sha512).hexdigest()

def str_params(params):
    return "".join(f"{quote(k)}={quote(str(v))}" for k, v in sorted(params.items()) if k in p and v not in [None, ""] and len(str(v)) <= 5000)

def get_sig(path, params): 
    return hmac512(path + hash256(str_params(params)), skey)

def get_cookie(force=False):
    global _cookie
    if _cookie and not force: return _cookie
    r = session.get(URL, timeout=10) 
    _cookie = "; ".join(f"{k}={v}" for k, v in r.cookies.items()) or None
    return _cookie

def zingmp3(path, extra=None):
    now = str(int(time.time()))
    params = {"ctime": now, "version": version, "apiKey": akey, **(extra or {})}
    params["sig"] = get_sig(path, params)
    cookie_header = get_cookie()
    headers = {"Cookie": cookie_header} if cookie_header else {}
    return session.get(f"{URL}{path}", headers=headers, params=params, timeout=10).json()

# api (CHO ZING)
chart_home = lambda: zingmp3("/api/v2/page/get/chart-home")
search_song = lambda q, count=10: zingmp3("/api/v2/search", {"q": q, "type": "song", "count": count, "allowCorrect": 1})
get_song = lambda song_id: zingmp3("/api/v2/song/get/info", {"id": song_id})
get_stream = lambda song_id: zingmp3("/api/v2/song/get/streaming", {"id": song_id})
get_lyric = lambda song_id: zingmp3("/api/v2/lyric/get/lyric", {"id": song_id})

# ===================================================================
# NỘI DUNG TỪ mcp_zingmp3.py (Phần còn lại)
# ===================================================================

# --- HÀM HỖ TRỢ PHÂN TÍCH LYRIC (CHO ZING) ---
def parse_lrc_to_json(lrc_content: str) -> List[Dict[str, Any]]:
    lines_json = []
    lrc_line_regex = re.compile(r'\[(\d{2}):(\d{2})[.:]?(\d{2,3})?\](.*)')
    
    for line in lrc_content.splitlines():
        match = lrc_line_regex.match(line)
        if match:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            hundredths = int(match.group(3) or 0)
            lyric_text = match.group(4).strip()
            
            start_time_ms = (minutes * 60 * 1000) + (seconds * 1000)
            if len(str(hundredths)) == 2:
                start_time_ms += (hundredths * 10)
            elif len(str(hundredths)) == 3:
                start_time_ms += hundredths

            if lyric_text:
                lines_json.append({
                    "startTime": start_time_ms,
                    "data": lyric_text
                })
    return lines_json
# --- KẾT THÚC HÀM HỖ TRỢ ---

# Khởi tạo máy chủ MCP
server = FastMCP("music-tools-server") 

# ===================================================================
# === CÔNG CỤ ZING MP3 (GIỮ NGUYÊN) ===
# ===================================================================

@server.tool()
def search_zing_songs(query: str, count: int = 5) -> List[Dict[str, str]]:
    """
    Tìm kiếm bài hát trên Zing MP3.
    Trả về một danh sách các bài hát khớp với từ khóa.
    """
    try:
        search_data = search_song(query, count=count) 
        if search_data.get("err", 0) != 0:
             print(f"Lỗi API Zing khi tìm kiếm: {search_data.get('msg')}", file=sys.stderr)
             return []
        if not search_data.get("data") or not search_data["data"].get("items"):
            return []
        
        songs_list = search_data["data"]["items"]
        results = []
        for song in songs_list:
            results.append({
                "id": song.get("encodeId"),
                "title": song.get("title"),
                "artists": song.get("artistsNames"),
                "thumbnail": song.get("thumbnailM")
            })
        return results
    except Exception as e:
        print(f"Lỗi khi tìm kiếm Zing MP3: {e}", file=sys.stderr)
        return []

@server.tool()
def get_zing_song_details(song_id: str) -> Dict[str, Any]:
    """
    Lấy thông tin chi tiết, link stream 128kbps và lời bài hát (dạng JSON)
    cho một song_id cụ thể của Zing MP3.
    """
    if not song_id:
        return {"error": "Thiếu song_id"}

    try:
        # 1. LẤY THÔNG TIN BÀI HÁT
        song_info = get_song(song_id)
        if song_info.get("err") != 0:
            return {"error": song_info.get("msg", "Lỗi khi lấy thông tin bài hát")}
        data = song_info.get("data", {})
        
        composers = data.get("composers", [])
        author_names = ", ".join([c["name"] for c in composers if c.get("name")]) or "Không rõ"

        # 2. LẤY STREAM
        stream_info = get_stream(song_id)
        if stream_info.get("err") != 0:
             print(f"Lỗi API Zing khi lấy stream: {stream_info.get('msg')}", file=sys.stderr)
             stream_url = f"Không thể lấy link (Lỗi: {stream_info.get('msg')})"
        else:
            stream_url = stream_info.get("data", {}).get("128")
            if not stream_url:
                stream_url = f"Không thể lấy link (Không có dữ liệu 128kbps)"
            elif stream_url == "VIP":
                stream_url = "Đây là bài hát VIP, cần tài khoản Premium."

        # 3. LẤY LYRIC
        lyric_info = get_lyric(song_id)
        lyric_json = []
        
        if lyric_info.get("err") == 0 and lyric_info.get("data"):
            lyric_data = lyric_info.get("data", {})
            if lyric_data.get("lines"):
                lyric_json = lyric_data["lines"]
            elif lyric_data.get("file"):
                file_url = lyric_data["file"]
                try:
                    resp = session.get(file_url, timeout=5) 
                    if resp.ok:
                        lrc_content = resp.text
                        lyric_json = parse_lrc_to_json(lrc_content)
                except Exception as e:
                    print(f"Lỗi khi phân tích file LRC (Zing): {e}", file=sys.stderr)

        full_details = {
            "id": data.get("encodeId"),
            "title": data.get("title"),
            "artists": data.get("artistsNames", "Không rõ"),
            "author": author_names,
            "thumbnail": data.get("thumbnailM"),
            "stream_url": stream_url,
            "lyric_json": lyric_json 
        }
        
        return full_details

    except Exception as e:
        print(f"Lỗi khi lấy chi tiết Zing: {e}", file=sys.stderr)
        return {"error": str(e)}

# ===================================================================
# === CÔNG CỤ YOUTUBE MUSIC (ĐÃ SỬA DÙNG YT-DLP) ===
# ===================================================================

@server.tool()
def search_youtube_music(query: str, count: int = 5) -> List[Dict[str, str]]:
    """
    Tìm kiếm bài hát trên YouTube Music.
    Trả về một danh sách các bài hát khớp với từ khóa.
    """
    global ytmusic
    if 'ytmusic' not in globals():
        return [{"error": "Thư viện YTMusic chưa được khởi tạo"}]

    try:
        # Chỉ tìm kiếm bài hát (songs)
        search_results = ytmusic.search(query=query, filter='songs', limit=count)
        
        results = []
        for song in search_results:
            artists = ", ".join([artist['name'] for artist in song.get('artists', [])])
            
            results.append({
                "id": song.get('videoId'),
                "title": song.get('title'),
                "artists": artists,
                "album": song.get('album', {}).get('name'),
                "duration": song.get('duration'),
                "thumbnail": song.get('thumbnails', [{}])[0].get('url') 
            })
        return results
    except Exception as e:
        print(f"Lỗi khi tìm kiếm YouTube Music: {e}", file=sys.stderr)
        return [{"error": str(e)}]

@server.tool()
def get_youtube_music_stream(video_id: str) -> Dict[str, Any]:
    """
    Lấy link stream (chỉ audio) cho một video_id từ YouTube.
    Sử dụng thư viện yt-dlp (để tránh lỗi 400).
    """
    if not video_id:
        return {"error": "Thiếu video_id"}

    try:
        video_url = f'https://www.youtube.com/watch?v={video_id}'
        
        # Cấu hình yt-dlp
        ydl_opts = {
            'format': 'bestaudio[ext=m4a]/bestaudio/best', # Lấy audio M4A tốt nhất, hoặc audio tốt nhất
            'quiet': True,        # Không in ra console
            'noplaylist': True,   # Không xử lý playlist
        }
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Lấy thông tin mà không tải về
            info = ydl.extract_info(video_url, download=False)
            
            if info:
                # Tìm định dạng (format) tốt nhất mà nó đã chọn
                audio_url = info.get('url') # Đây là link stream trực tiếp
                
                if not audio_url:
                    # Đôi khi link nằm trong 'formats'
                    for f in info.get('formats', []):
                         if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                             audio_url = f.get('url')
                             break
                
                if audio_url:
                    return {
                        "id": video_id,
                        "title": info.get('title'),
                        "author": info.get('uploader', 'Không rõ'),
                        "thumbnail": info.get('thumbnail'),
                        "stream_url": audio_url, # URL đã có chữ ký
                        "abr": info.get('abr', 'Không rõ') # Audio Bitrate
                    }
                else:
                    return {"error": "Không tìm thấy audio stream trong thông tin yt-dlp."}
            else:
                return {"error": "yt-dlp không thể lấy thông tin video."}
            
    except Exception as e:
        print(f"Lỗi khi lấy stream YouTube (yt-dlp): {e}", file=sys.stderr)
        return {"error": str(e)}

# ===================================================================
# === HÀM MAIN (KHỞI ĐỘNG SERVER) ===
# ===================================================================

def main():
    """Hàm main để chạy server."""
    print("Đang khởi động Music MCP Server (Zing + YouTube [yt-dlp])...")
    server.run()

if __name__ == "__main__":
    main()
