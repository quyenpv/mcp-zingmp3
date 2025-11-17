# File: mcp_music_stream.py
# MCP Server for ESP32 Music Player
# Tích hợp ZingMP3 + YouTube Music -> MP3 Stream URL

import mcp.types as types
from mcp.server.fastmcp import FastMCP
import re
import requests
import json
import sys
import time
import hashlib
import hmac
from urllib.parse import quote
from typing import List, Dict, Any
import cloudscraper

try:
    from ytmusicapi import YTMusic
    import yt_dlp
except ImportError:
    print("LỖI: Thiếu thư viện ytmusicapi hoặc yt-dlp", file=sys.stderr)
    sys.exit(1)

# =================================================================
# ZING MP3 CONFIGURATION (FIX CỨNG)
# =================================================================
ZING_URL = "https://zingmp3.vn"
ZING_VERSION = "1.16.5"
ZING_AKEY = "X5BM3w8N7MKozC0B85o4KMlzLZKhV00y"
ZING_SKEY = "acOrvUS15XRW2o9JksiK1KgQ6Vbds8ZW"

session = cloudscraper.create_scraper()
_cookie = None

def hash256(s): 
    return hashlib.sha256(s.encode()).hexdigest()

def hmac512(s, key): 
    return hmac.new(key.encode(), s.encode(), hashlib.sha512).hexdigest()

def str_params(params):
    p = {"ctime", "id", "type", "page", "count", "version"}
    return "".join(f"{quote(k)}={quote(str(v))}" for k, v in sorted(params.items()) 
                   if k in p and v not in [None, ""] and len(str(v)) <= 5000)

def get_sig(path, params): 
    return hmac512(path + hash256(str_params(params)), ZING_SKEY)

def get_cookie(force=False):
    global _cookie
    if _cookie and not force: return _cookie
    r = session.get(ZING_URL, timeout=10)
    _cookie = "; ".join(f"{k}={v}" for k, v in r.cookies.items()) or None
    return _cookie

def zingmp3(path, extra=None):
    now = str(int(time.time()))
    params = {"ctime": now, "version": ZING_VERSION, "apiKey": ZING_AKEY, **(extra or {})}
    params["sig"] = get_sig(path, params)
    cookie_header = get_cookie()
    headers = {"Cookie": cookie_header} if cookie_header else {}
    return session.get(f"{ZING_URL}{path}", headers=headers, params=params, timeout=10).json()

# Zing API
search_song = lambda q, count=10: zingmp3("/api/v2/search", {"q": q, "type": "song", "count": count, "allowCorrect": 1})
get_song = lambda song_id: zingmp3("/api/v2/song/get/info", {"id": song_id})
get_stream = lambda song_id: zingmp3("/api/v2/song/get/streaming", {"id": song_id})
get_lyric = lambda song_id: zingmp3("/api/v2/lyric/get/lyric", {"id": song_id})

# =================================================================
# YOUTUBE MUSIC CONFIGURATION
# =================================================================
try:
    ytmusic = YTMusic()
except Exception as e:
    print(f"LỖI: Không thể khởi tạo YTMusic: {e}", file=sys.stderr)

# =================================================================
# KHỞI TẠO MCP SERVER
# =================================================================
server = FastMCP("esp32-music-stream-server")

# =================================================================
# TOOL 1: TÌM KIẾM NHẠC (ZING + YOUTUBE)
# =================================================================
@server.tool()
def search_music(query: str, source: str = "auto", count: int = 5) -> List[Dict[str, Any]]:
    """
    Tìm kiếm bài hát từ ZingMP3 hoặc YouTube Music.
    
    Args:
        query: Từ khóa tìm kiếm (tên bài hát, nghệ sĩ)
        source: Nguồn tìm kiếm - "zing", "youtube", hoặc "auto" (cả 2)
        count: Số lượng kết quả (mặc định 5)
    
    Returns:
        Danh sách bài hát với id, title, artists, source
    """
    results = []
    
    # Tìm trên Zing MP3
    if source in ["zing", "auto"]:
        try:
            zing_data = search_song(query, count=count)
            if zing_data.get("err") == 0 and zing_data.get("data", {}).get("items"):
                for song in zing_data["data"]["items"]:
                    results.append({
                        "id": song.get("encodeId"),
                        "title": song.get("title"),
                        "artists": song.get("artistsNames"),
                        "thumbnail": song.get("thumbnailM"),
                        "source": "zing",
                        "duration": song.get("duration", 0)
                    })
        except Exception as e:
            print(f"Lỗi tìm kiếm Zing: {e}", file=sys.stderr)
    
    # Tìm trên YouTube Music
    if source in ["youtube", "auto"]:
        try:
            yt_results = ytmusic.search(query=query, filter='songs', limit=count)
            for song in yt_results:
                artists = ", ".join([artist['name'] for artist in song.get('artists', [])])
                results.append({
                    "id": song.get('videoId'),
                    "title": song.get('title'),
                    "artists": artists,
                    "thumbnail": song.get('thumbnails', [{}])[0].get('url'),
                    "source": "youtube",
                    "duration": song.get('duration', 'N/A')
                })
        except Exception as e:
            print(f"Lỗi tìm kiếm YouTube: {e}", file=sys.stderr)
    
    return results

# =================================================================
# TOOL 2: LẤY LINK MP3 STREAM (CORE FUNCTION)
# =================================================================
@server.tool()
def get_mp3_stream_url(song_id: str, source: str) -> Dict[str, Any]:
    """
    Lấy link MP3 stream trực tiếp cho ESP32.
    
    Args:
        song_id: ID bài hát (encodeId từ Zing hoặc videoId từ YouTube)
        source: Nguồn - "zing" hoặc "youtube"
    
    Returns:
        Dict với stream_url (MP3), song_name, lyric_url, duration
    """
    
    # === ZING MP3 ===
    if source == "zing":
        try:
            # 1. Lấy thông tin bài hát
            song_info = get_song(song_id)
            if song_info.get("err") != 0:
                return {"error": f"Lỗi Zing API: {song_info.get('msg')}"}
            
            data = song_info.get("data", {})
            song_name = data.get("title", "Unknown")
            
            # 2. Lấy stream URL (128kbps MP3)
            stream_info = get_stream(song_id)
            if stream_info.get("err") != 0:
                return {"error": f"Không thể lấy stream: {stream_info.get('msg')}"}
            
            stream_url = stream_info.get("data", {}).get("128")
            if not stream_url or stream_url == "VIP":
                return {"error": "Bài hát VIP hoặc không có link 128kbps"}
            
            # 3. Lấy lyric (optional)
            lyric_url = None
            try:
                lyric_info = get_lyric(song_id)
                if lyric_info.get("err") == 0:
                    lyric_data = lyric_info.get("data", {})
                    lyric_url = lyric_data.get("file")
            except:
                pass
            
            return {
                "success": True,
                "stream_url": stream_url,
                "song_name": song_name,
                "artists": data.get("artistsNames", "Unknown"),
                "lyric_url": lyric_url or "",
                "duration": data.get("duration", 0),
                "source": "zing",
                "format": "mp3"
            }
            
        except Exception as e:
            return {"error": f"Lỗi Zing: {str(e)}"}
    
    # === YOUTUBE MUSIC ===
    elif source == "youtube":
        try:
            video_url = f'https://www.youtube.com/watch?v={song_id}'
            
            # Cấu hình yt-dlp: ưu tiên M4A/MP3
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]/bestaudio/best',
                'quiet': True,
                'noplaylist': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                
                if not info:
                    return {"error": "Không thể lấy thông tin video"}
                
                # Lấy URL stream trực tiếp
                audio_url = info.get('url')
                
                # Nếu không có, tìm trong formats
                if not audio_url:
                    for f in info.get('formats', []):
                        if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                            audio_url = f.get('url')
                            break
                
                if not audio_url:
                    return {"error": "Không tìm thấy audio stream"}
                
                return {
                    "success": True,
                    "stream_url": audio_url,
                    "song_name": info.get('title', 'Unknown'),
                    "artists": info.get('uploader', 'Unknown'),
                    "lyric_url": "",  # YouTube không có lyric sẵn
                    "duration": info.get('duration', 0),
                    "source": "youtube",
                    "format": info.get('ext', 'm4a'),
                    "bitrate": info.get('abr', 0)
                }
                
        except Exception as e:
            return {"error": f"Lỗi YouTube: {str(e)}"}
    
    else:
        return {"error": "Source không hợp lệ, chỉ chấp nhận 'zing' hoặc 'youtube'"}

# =================================================================
# TOOL 3: WORKFLOW TỰ ĐỘNG (TÌM + PHÁT)
# =================================================================
@server.tool()
def auto_play_music(query: str, source: str = "auto") -> Dict[str, Any]:
    """
    Workflow tự động: Tìm kiếm -> Lấy bài đầu tiên -> Trả về stream URL.
    Dùng tool này khi user nói "phát bài [tên bài hát]".
    
    Args:
        query: Tên bài hát hoặc nghệ sĩ
        source: Nguồn ưu tiên - "zing", "youtube", hoặc "auto"
    
    Returns:
        Dict với stream_url, song_name, lyric_url để gọi self.music.play_stream_url
    """
    # 1. Tìm kiếm
    search_results = search_music(query, source, count=1)
    
    if not search_results:
        return {"error": f"Không tìm thấy bài hát '{query}'"}
    
    # 2. Lấy bài đầu tiên
    first_song = search_results[0]
    song_id = first_song["id"]
    song_source = first_song["source"]
    
    # 3. Lấy stream URL
    stream_data = get_mp3_stream_url(song_id, song_source)
    
    if "error" in stream_data:
        # Nếu source đầu tiên lỗi, thử source còn lại
        if source == "auto" and len(search_results) > 0:
            # Thử bài tiếp theo nếu có
            for song in search_results[1:]:
                stream_data = get_mp3_stream_url(song["id"], song["source"])
                if "success" in stream_data:
                    break
        
        if "error" in stream_data:
            return stream_data
    
    # 4. Format response cho ESP32
    return {
        "success": True,
        "stream_url": stream_data["stream_url"],
        "song_name": stream_data["song_name"],
        "artists": stream_data["artists"],
        "lyric_url": stream_data.get("lyric_url", ""),
        "duration": stream_data.get("duration", 0),
        "source": stream_data["source"],
        "message": f"✅ Sẵn sàng phát: {stream_data['song_name']} - {stream_data['artists']}"
    }

# =================================================================
# TOOL 4: LẤY THÔNG TIN CHI TIẾT
# =================================================================
@server.tool()
def get_song_details(song_id: str, source: str) -> Dict[str, Any]:
    """
    Lấy thông tin chi tiết bài hát (không bao gồm stream URL).
    
    Args:
        song_id: ID bài hát
        source: Nguồn - "zing" hoặc "youtube"
    
    Returns:
        Dict với title, artists, album, thumbnail, duration
    """
    if source == "zing":
        try:
            song_info = get_song(song_id)
            if song_info.get("err") != 0:
                return {"error": song_info.get("msg")}
            
            data = song_info.get("data", {})
            composers = data.get("composers", [])
            author_names = ", ".join([c["name"] for c in composers if c.get("name")]) or "Không rõ"
            
            return {
                "id": data.get("encodeId"),
                "title": data.get("title"),
                "artists": data.get("artistsNames", "Không rõ"),
                "author": author_names,
                "album": data.get("album", {}).get("title", "N/A"),
                "thumbnail": data.get("thumbnailM"),
                "duration": data.get("duration", 0),
                "source": "zing"
            }
        except Exception as e:
            return {"error": str(e)}
    
    elif source == "youtube":
        try:
            video_url = f'https://www.youtube.com/watch?v={song_id}'
            ydl_opts = {'quiet': True, 'noplaylist': True}
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                
                return {
                    "id": song_id,
                    "title": info.get('title'),
                    "artists": info.get('uploader', 'Unknown'),
                    "album": info.get('album', 'N/A'),
                    "thumbnail": info.get('thumbnail'),
                    "duration": info.get('duration', 0),
                    "view_count": info.get('view_count', 0),
                    "source": "youtube"
                }
        except Exception as e:
            return {"error": str(e)}
    
    return {"error": "Source không hợp lệ"}

# =================================================================
# MAIN
# =================================================================
def main():
    """Khởi động MCP server"""
    print("🎵 Đang khởi động ESP32 Music Stream MCP Server...")
    print("📡 Hỗ trợ: ZingMP3 + YouTube Music -> MP3 Stream")
    server.run()

if __name__ == "__main__":
    main()
