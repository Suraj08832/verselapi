import os
import requests
from http.cookiejar import CookieJar
from flask import Flask, request, jsonify
from flask_caching import Cache
from youtube_search import YoutubeSearch
import yt_dlp

# -----------------------------------------------------------------------------
# FORCE yt-dlp to use /tmp for all its cache & cookies, and disable saving
# -----------------------------------------------------------------------------
# Redirect XDG cache to /tmp so yt-dlp’s default ~/.cache writes go to /tmp/.cache
os.environ['XDG_CACHE_HOME'] = '/tmp'

# -----------------------------------------------------------------------------
# Hard‑coded Cookies Setup
# -----------------------------------------------------------------------------
COOKIE_VALUES = {
    'HSID': 'AlKMxCmGETuUEsp1_',
    'SSID': 'AB64ZeQGzzRHKE_Z6',
    'APISID': 'EiB0VDTpxjSDvgT4/AmFgDk-WWb_UosYdr',
    'SAPISID': '9QuIQ38t88n6lTPC/A-5OB6qOM2U5jk0SM',
    '__Secure-1PAPISID': '9QuIQ38t88n6lTPC/A-5OB6qOM2U5jk0SM',
    '__Secure-3PAPISID': '9QuIQ38t88n6lTPC/A-5OB6qOM2U5jk0SM',
    'LOGIN_INFO': 'AFmmF2swRQIhAKz4V3Xd227B7WSs9j3TsxAD0-4EyuWRZ3RaQZGYypwsAiAomdFL3ILDR_STpUDNpzMkjdweP2BwQgg2XiJbk4BNvg:…',
    '__Secure-1PSIDTS': 'sidts-CjIB5H03P9QCErEJjeacOqKtDkwmy2EuEzB5FKO9R5g-4Lrb4kNIWwIHRXD5l0x1Qcmv9xAA',
    '__Secure-3PSIDTS': 'sidts-CjIB5H03P9QCErEJjeacOqKtDkwmy2EuEzB5FKO9R5g-4Lrb4kNIWwIHRXD5l0x1Qcmv9xAA',
    'SID': 'g.a000yggHu2cvDjzkyzLGDFpGIOM7cSyt0jBaAEwtJSDhwUz7P7lym5OZNnXjAKuiGufceOlGeQACgYKAZASARESFQHGX2MiNWRWZmK_nlUa-I4_04vLuxoVAUF8yKpe0JDFVnUcmsmFbdzZ2Dgy0076',
    '__Secure-1PSID': 'g.a000yggHu2cvDjzkyzLGDFpGIOM7cSyt0jBaAEwtJSDhwUz7P7lyqpjlWpI7BisPMZl9wyUtEwACgYKAbASARESFQHGX2MiCrZCmAFB7SL-wz2tT4NgpxoVAUF8yKrjed1Nw8Gabz2KSoYeTIBz0076',
    '__Secure-3PSID': 'g.a000yggHu2cvDjzkyzLGDFpGIOM7cSyt0jBaAEwtJSDhwUz7P7lyWUiftngWwTkiz5zRpoCT-QACgYKAQ0SARESFQHGX2MiBjGEm969ztu0-M_FyoBkIhoVAUF8yKpcO6OqxylgxcPmj4xRFwQU0076',
    'PREF': 'f6=40000000&tz=Asia.Colombo',
    'ST-3opvp5': 'session_logininfo=AFmmF2swRQIhAKz4V3Xd227B7WSs9j3TsxAD0-4EyuWRZ3RaQZGYypwsAiAomdFL3ILDR_STpUDNpzMkjdweP2BwQgg2XiJbk4BNvg%3A…',
    'SIDCC': 'AKEyXzWzy28BoNuI4CMv69tnHcdz4VMxy2p35aWQlH_zwre7AGBoAjdihXCt8Zro3KHNv7RI',
    '__Secure-1PSIDCC': 'AKEyXzWv6PlzpF648TXQ3WsnOuBZu1aTRaF-o5R3s49SFqP0CyXtEJlcWvgiCFNHQ4R7JcCCIQ',
    '__Secure-3PSIDCC': 'AKEyXzVSdHRpD1oYl4zX8LjBBNvIHCO2eYL6prRtpc7ijxxR4-W6hPXfVtl2eEL7lKPHljZsLg',
    'YSC': 'UgdL8arXsKI',
    'VISITOR_INFO1_LIVE': 'PSQSCixrKKE',
    'VISITOR_PRIVACY_METADATA': 'CgJJThIEGgAgIg%3D%3D',
    '__Secure-ROLLOUT_TOKEN': 'CM3mv7nHobyXSxDhxIKuyJaOAxjSrfauyJaOAw%3D%3D'
}

session = requests.Session()
for name, value in COOKIE_VALUES.items():
    session.cookies.set(name, value, domain=".youtube.com", path="/")

# Monkey-patch requests.get to always include these cookies
_original_get = requests.get
def get_with_cookies(url, **kwargs):
    kwargs.setdefault('cookies', session.cookies)
    return _original_get(url, **kwargs)
requests.get = get_with_cookies

# -------------------------
# Flask App Initialization
# -------------------------
app = Flask(__name__)

# -------------------------
# Cache Configuration
# -------------------------
cache = Cache(app, config={
    'CACHE_TYPE': 'simple',
    'CACHE_DEFAULT_TIMEOUT': 0  # default “infinite” for manual caching
})

# -------------------------
# Helper: Convert durations to ISO 8601
# -------------------------
def to_iso_duration(duration_str: str) -> str:
    parts = duration_str.split(':') if duration_str else []
    iso = 'PT'
    if len(parts) == 3:
        h, m, s = parts
        if int(h): iso += f"{int(h)}H"
        iso += f"{int(m)}M{int(s)}S"
    elif len(parts) == 2:
        m, s = parts
        iso += f"{int(m)}M{int(s)}S"
    elif len(parts) == 1 and parts[0].isdigit():
        iso += f"{int(parts[0])}S"
    else:
        iso += '0S'
    return iso

# -------------------------
# yt-dlp Options and Extraction
# -------------------------
ydl_opts_full = {
    'quiet': True,
    'skip_download': True,
    'format': 'bestvideo+bestaudio/best',
    'save_cookies': False,
    'cachedir': False,
}
ydl_opts_meta = {
    'quiet': True,
    'skip_download': True,
    'simulate': True,
    'noplaylist': True,
    'save_cookies': False,
    'cachedir': False,
}

def extract_info(url=None, search_query=None, opts=None):
    ydl_opts = opts or ydl_opts_full
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        if search_query:
            result = ydl.extract_info(f"ytsearch:{search_query}", download=False)
            entries = result.get('entries')
            if not entries:
                return None, {'error': 'No search results'}, 404
            return entries[0], None, None
        else:
            info = ydl.extract_info(url, download=False)
            return info, None, None

# -------------------------
# Format Helpers for yt-dlp
# -------------------------
def get_size_bytes(fmt):
    return fmt.get('filesize') or fmt.get('filesize_approx') or 0

def format_size(bytes_val):
    if bytes_val >= 1e9: return f"{bytes_val/1e9:.2f} GB"
    if bytes_val >= 1e6: return f"{bytes_val/1e6:.2f} MB"
    if bytes_val >= 1e3: return f"{bytes_val/1e3:.2f} KB"
    return f"{bytes_val} B"

def build_formats_list(info):
    fmts = []
    for f in info.get('formats', []):
        url_f = f.get('url')
        if not url_f: continue
        has_video = f.get('vcodec') != 'none'
        has_audio = f.get('acodec') != 'none'
        kind = 'progressive' if has_video and has_audio else \
               'video-only' if has_video else \
               'audio-only' if has_audio else None
        if not kind: continue
        size = get_size_bytes(f)
        fmts.append({
            'format_id': f.get('format_id'),
            'ext': f.get('ext'),
            'kind': kind,
            'filesize_bytes': size,
            'filesize': format_size(size),
            'width': f.get('width'),
            'height': f.get('height'),
            'fps': f.get('fps'),
            'abr': f.get('abr'),
            'asr': f.get('asr'),
            'url': url_f
        })
    return fmts

# -------------------------
# Flask Routes with Manual Caching for Metadata
# -------------------------
@app.route('/')
def home():
    key = 'home'
    if 'latest' in request.args:
        cache.delete(key)
    data = cache.get(key)
    if data:
        return jsonify(data)
    data = {'message': '✅ YouTube API is alive'}
    cache.set(key, data)
    return jsonify(data)

@app.route('/api/fast-meta')
def api_fast_meta():
    q = request.args.get('search', '').strip()
    u = request.args.get('url', '').strip()
    key = f"fast_meta:{q}:{u}"
    if 'latest' in request.args:
        cache.delete(key)
    cached = cache.get(key)
    if cached is not None:
        return jsonify(cached)
    if not q and not u:
        return jsonify({'error': 'Provide either "search" or "url" parameter'}), 400
    result = None
    try:
        if q:
            results = YoutubeSearch(q, max_results=1).to_dict()
            if results:
                vid = results[0]
                result = {
                    'title': vid.get('title'),
                    'link': f"https://www.youtube.com/watch?v={vid.get('url_suffix').split('v=')[-1]}",
                    'duration': to_iso_duration(vid.get('duration', '')),
                    'thumbnail': vid.get('thumbnails', [None])[0]
                }
        else:
            with yt_dlp.YoutubeDL(ydl_opts_meta) as ydl:
                info = ydl.extract_info(u, download=False)
            result = {
                'title': info.get('title'),
                'link': info.get('webpage_url'),
                'duration': to_iso_duration(str(info.get('duration'))),
                'thumbnail': info.get('thumbnail')
            }
        if not result:
            return jsonify({'error': 'No results'}), 404
        cache.set(key, result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/all')
def api_all():
    q = request.args.get('search', '').strip()
    u = request.args.get('url', '').strip()
    if not (q or u):
        return jsonify({'error': 'Provide "url" or "search"'}), 400
    info, err, code = extract_info(u or None, q or None)
    if err:
        return jsonify(err), code
    fmts = build_formats_list(info)
    suggestions = [
        {'id': rel.get('id'),
         'title': rel.get('title'),
         'url': rel.get('webpage_url') or rel.get('url'),
         'thumbnail': rel.get('thumbnails', [{}])[0].get('url')}
        for rel in info.get('related', [])
    ]
    data = {
        'title': info.get('title'),
        'video_url': info.get('webpage_url'),
        'duration': info.get('duration'),
        'upload_date': info.get('upload_date'),
        'view_count': info.get('view_count'),
        'like_count': info.get('like_count'),
        'thumbnail': info.get('thumbnail'),
        'description': info.get('description'),
        'tags': info.get('tags'),
        'is_live': info.get('is_live'),
        'age_limit': info.get('age_limit'),
        'average_rating': info.get('average_rating'),
        'channel': {
            'name': info.get('uploader'),
            'url': info.get('uploader_url') or info.get('channel_url'),
            'id': info.get('uploader_id')
        },
        'formats': fmts,
        'suggestions': suggestions
    }
    return jsonify(data)

@app.route('/api/meta')
def api_meta():
    q = request.args.get('search', '').strip()
    u = request.args.get('url', '').strip()
    key = f"meta:{q}:{u}"
    if 'latest' in request.args:
        cache.delete(key)
    cached = cache.get(key)
    if cached:
        return jsonify(cached)
    if not (q or u):
        return jsonify({'error': 'Provide "url" or "search" parameter'}), 400
    info, err, code = extract_info(u or None, q or None, opts=ydl_opts_meta)
    if err:
        return jsonify(err), code
    keys = ['id','title','webpage_url','duration','upload_date',
            'view_count','like_count','thumbnail','description',
            'tags','is_live','age_limit','average_rating',
            'uploader','uploader_url','uploader_id']
    data = {'metadata': {k: info.get(k) for k in keys}}
    cache.set(key, data)
    return jsonify(data)

@app.route('/api/channel')
def api_channel():
    cid = request.args.get('id', '').strip()
    cu = request.args.get('url', '').strip()
    key = f"channel:{cid or cu}"
    if 'latest' in request.args:
        cache.delete(key)
    cached = cache.get(key)
    if cached:
        return jsonify(cached)
    if not (cid or cu):
        return jsonify({'error': 'Provide "url" or "id" parameter for channel'}), 400
    try:
        with yt_dlp.YoutubeDL(ydl_opts_meta) as ydl:
            info = ydl.extract_info(cid or cu, download=False)
        data = {
            'id': info.get('id'),
            'name': info.get('uploader'),
            'url': info.get('webpage_url'),
            'description': info.get('description'),
            'subscriber_count': info.get('subscriber_count'),
            'video_count': info.get('channel_follower_count') or info.get('video_count'),
            'thumbnails': info.get('thumbnails'),
        }
        cache.set(key, data)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/playlist')
def api_playlist():
    pid = request.args.get('id', '').strip()
    pu = request.args.get('url', '').strip()
    key = f"playlist:{pid or pu}"
    if 'latest' in request.args:
        cache.delete(key)
    cached = cache.get(key)
    if cached:
        return jsonify(cached)
    if not (pid or pu):
        return jsonify({'error': 'Provide "url" or "id" parameter for playlist'}), 400
    try:
        with yt_dlp.YoutubeDL(ydl_opts_full) as ydl:
            info = ydl.extract_info(pid or pu, download=False)
        videos = [{
            'id': e.get('id'),
            'title': e.get('title'),
            'url': e.get('webpage_url'),
            'duration': e.get('duration')
        } for e in info.get('entries', [])]
        data = {
            'id': info.get('id'),
            'title': info.get('title'),
            'url': info.get('webpage_url'),
            'item_count': info.get('playlist_count'),
            'videos': videos
        }
        cache.set(key, data)
        return jsonify(data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/instagram')
def api_instagram():
    u = request.args.get('url', '').strip()
    key = f"instagram:{u}"
    if 'latest' in request.args:
        cache.delete(key)
    cached = cache.get(key)
    if cached:
        return jsonify(cached)
    if not u:
        return jsonify({'error': 'Provide "url" parameter for Instagram'}), 400
    try:
        with yt_dlp.YoutubeDL(ydl_opts_meta) as ydl:
            info = ydl.extract_info(u, download=False)
        cache.set(key, info)
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/twitter')
def api_twitter():
    u = request.args.get('url', '').strip()
    key = f"twitter:{u}"
    if 'latest' in request.args:
        cache.delete(key)
    cached = cache.get(key)
    if cached:
        return jsonify(cached)
    if not u:
        return jsonify({'error': 'Provide "url" parameter for Twitter'}), 400
    try:
        with yt_dlp.YoutubeDL(ydl_opts_meta) as ydl:
            info = ydl.extract_info(u, download=False)
        cache.set(key, info)
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/tiktok')
def api_tiktok():
    u = request.args.get('url', '').strip()
    key = f"tiktok:{u}"
    if 'latest' in request.args:
        cache.delete(key)
    cached = cache.get(key)
    if cached:
        return jsonify(cached)
    if not u:
        return jsonify({'error': 'Provide "url" parameter for TikTok'}), 400
    try:
        with yt_dlp.YoutubeDL(ydl_opts_meta) as ydv:
            info = ydv.extract_info(u, download=False)
        cache.set(key, info)
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/facebook')
def api_facebook():
    u = request.args.get('url', '').strip()
    key = f"facebook:{u}"
    if 'latest' in request.args:
        cache.delete(key)
    cached = cache.get(key)
    if cached:
        return jsonify(cached)
    if not u:
        return jsonify({'error': 'Provide "url" parameter for Facebook'}), 400
    try:
        with yt_dlp.YoutubeDL(ydl_opts_meta) as ydl:
            info = ydl.extract_info(u, download=False)
        cache.set(key, info)
        return jsonify(info)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# -------------------------
# Stream Endpoints (no caching)
# -------------------------
STREAM_TIMEOUT = 5 * 3600

@app.route('/download')
@cache.cached(timeout=STREAM_TIMEOUT, key_prefix=lambda: f"download:{request.full_path}")
def api_download():
    url = request.args.get('url')
    search = request.args.get('search')
    if not (url or search):
        return jsonify({'error': 'Provide "url" or "search"'}), 400
    info, err, code = extract_info(url, search)
    if err:
        return jsonify(err), code
    return jsonify({'formats': build_formats_list(info)})

@app.route('/api/audio')
def api_audio():
    url = request.args.get('url')
    search = request.args.get('search')
    if not (url or search):
        return jsonify({'error': 'Provide "url" or "search"'}), 400
    info, err, code = extract_info(url, search)
    if err:
        return jsonify(err), code
    afmts = [f for f in build_formats_list(info) if f['kind'] in ('audio-only','progressive')]
    return jsonify({'audio_formats': afmts})

@app.route('/api/video')
def api_video():
    url = request.args.get('url')
    search = request.args.get('search')
    if not (url or search):
        return jsonify({'error': 'Provide "url" or "search"'}), 400
    info, err, code = extract_info(url, search)
    if err:
        return jsonify(err), code
    vfmts = [f for f in build_formats_list(info) if f['kind'] in ('video-only','progressive')]
    return jsonify({'video_formats': vfmts})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

