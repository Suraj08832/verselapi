import os
import requests
from http.cookiejar import MozillaCookieJar
from flask import Flask, request, jsonify
from flask_caching import Cache
from youtube_search import YoutubeSearch
import yt_dlp
import yt_dlp.utils

# -------------------------
# Ensure writable tmp directory for yt-dlp cache & cookies
# -------------------------
tmp_dir = '/tmp/yt_dlp'
os.makedirs(tmp_dir, exist_ok=True)
# Redirect default cache home to tmp to avoid RO filesystem errors
os.environ['XDG_CACHE_HOME'] = tmp_dir
cookie_file = os.path.join(tmp_dir, 'cookies.txt')

# -------------------------
# Load Cookies and Patch requests.get (if cookie_file present)
# -------------------------
if os.path.exists(cookie_file):
    cookie_jar = MozillaCookieJar(cookie_file)
    cookie_jar.load(ignore_discard=True, ignore_expires=True)
    session = requests.Session()
    session.cookies = cookie_jar
    original_get = requests.get
    def get_with_cookies(url, **kwargs):
        kwargs.setdefault('cookies', session.cookies)
        return original_get(url, **kwargs)
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
    'CACHE_DEFAULT_TIMEOUT': 0
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
common_opts = {
    'quiet': True,
    'skip_download': True,
    'cookiefile': cookie_file,
    'nocache-dir': True,
    'cache_dir': tmp_dir
}
ydl_opts_full = {**common_opts, 'format': 'bestvideo+bestaudio/best'}
ydl_opts_meta = {**common_opts, 'simulate': True, 'noplaylist': True}

# Extract info with graceful error handling

def extract_info(url=None, search_query=None, opts=None):
    ydl_opts = opts or ydl_opts_full
    try:
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
    except yt_dlp.utils.DownloadError as e:
        msg = str(e)
        if 'Sign in to confirm' in msg:
            return None, {'error': 'Authentication required: supply valid YouTube cookies.txt'}, 403
        return None, {'error': msg}, 500

# -------------------------
# Format Helpers
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
# Flask Routes
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
        return jsonify({'error': 'Provide "url" or "search"'}), 400
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
