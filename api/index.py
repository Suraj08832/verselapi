import os
import tempfile
import requests
from http.cookiejar import MozillaCookieJar
from flask import Flask, request, jsonify
from flask_caching import Cache
from youtube_search import YoutubeSearch
import yt_dlp
import logging
import datetime

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# -------------------------
# API Key Configuration
# -------------------------
API_KEY = "zefron@123"

def require_api_key(f):
    def decorated_function(*args, **kwargs):
        api_key = request.args.get('api_key') or request.headers.get('X-API-Key')
        if api_key != API_KEY:
            return jsonify({'error': 'Invalid or missing API key'}), 401
        return f(*args, **kwargs)
    decorated_function.__name__ = f.__name__
    return decorated_function

# -------------------------
# Use Temp Directory for All File Operations (Vercel Compatibility)
# -------------------------
# Determine writable temp directory
temp_dir = os.environ.get('TMPDIR', tempfile.gettempdir())
# Paths for cookie storage
cookie_file = os.path.join(temp_dir, 'cookies.txt')
cookies_file = cookie_file

# -------------------------
# Load Cookies and Patch requests.get
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
# Cache Configuration (In-Memory)
# -------------------------
cache = Cache(app, config={
    'CACHE_TYPE': 'simple',  # In-memory
    'CACHE_DEFAULT_TIMEOUT': 0  # "Infinite" until invalidated
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
    'cookiefile': cookies_file,
    # Disable filesystem caching or direct to temporary cache dir
    'cachedir': False
}
ydl_opts_meta = {
    'quiet': True,
    'skip_download': True,
    'simulate': True,
    'noplaylist': True,
    'cookiefile': cookies_file
}

def extract_info(url=None, search_query=None, opts=None):
    ydl_opts = opts or ydl_opts_full
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            if search_query:
                # For search queries, first get search results
                result = ydl.extract_info(f"ytsearch:{search_query}", download=False)
                entries = result.get('entries')
                if not entries:
                    return None, {'error': 'No search results'}, 404
                
                # Get the first video ID from search results
                first_video = entries[0]
                video_id = first_video.get('id')
                
                if not video_id:
                    return None, {'error': 'Invalid video ID from search'}, 404
                
                # Now extract full info for this specific video
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                info = ydl.extract_info(video_url, download=False)
                return info, None, None
            else:
                # Direct URL provided
                info = ydl.extract_info(url, download=False)
                return info, None, None
    except Exception as e:
        return None, {'error': f'Extraction failed: {str(e)}'}, 500

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
# Flask Routes (with Manual Caching)
# -------------------------
@app.route('/')
@require_api_key
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
@require_api_key
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
@require_api_key
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
@require_api_key
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
@require_api_key
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
@require_api_key
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
@require_api_key
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
@require_api_key
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
@require_api_key
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
@require_api_key
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
@require_api_key
def api_download():
    try:
        url = request.args.get('url')
        search = request.args.get('search')
        
        if not (url or search):
            return jsonify({'error': 'Provide "url" or "search" parameter'}), 400
        
        logging.info(f"Download request - URL: {url}, Search: {search}")
        
        info, err, code = extract_info(url, search)
        if err:
            logging.error(f"Extract info failed: {err}")
            return jsonify(err), code
        
        if not info:
            logging.error("No video info extracted")
            return jsonify({'error': 'Failed to extract video information'}), 500
        
        formats = build_formats_list(info)
        if not formats:
            logging.warning("No formats found for video")
            return jsonify({'error': 'No download formats available', 'formats': []}), 404
        
        logging.info(f"Successfully extracted {len(formats)} formats")
        return jsonify({
            'formats': formats,
            'title': info.get('title'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail')
        })
        
    except Exception as e:
        logging.error(f"Exception in download endpoint: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@app.route('/api/audio')
@require_api_key
def api_audio():
    try:
        url = request.args.get('url')
        search = request.args.get('search')
        
        if not (url or search):
            return jsonify({'error': 'Provide "url" or "search" parameter'}), 400
        
        logging.info(f"Audio request - URL: {url}, Search: {search}")
        
        info, err, code = extract_info(url, search)
        if err:
            logging.error(f"Extract info failed: {err}")
            return jsonify(err), code
        
        if not info:
            logging.error("No video info extracted")
            return jsonify({'error': 'Failed to extract video information'}), 500
        
        # Get all formats and filter for audio
        all_formats = build_formats_list(info)
        audio_formats = [f for f in all_formats if f['kind'] in ('audio-only', 'progressive')]
        
        if not audio_formats:
            logging.warning("No audio formats found for video")
            return jsonify({'error': 'No audio formats available', 'audio_formats': []}), 404
        
        # Sort by quality (bitrate)
        audio_formats.sort(key=lambda x: x.get('abr', 0), reverse=True)
        
        logging.info(f"Successfully extracted {len(audio_formats)} audio formats")
        return jsonify({
            'audio_formats': audio_formats,
            'title': info.get('title'),
            'duration': info.get('duration'),
            'thumbnail': info.get('thumbnail'),
            'best_audio_url': audio_formats[0]['url'] if audio_formats else None
        })
        
    except Exception as e:
        logging.error(f"Exception in audio endpoint: {str(e)}")
        return jsonify({'error': f'Internal server error: {str(e)}'}), 500

@app.route('/api/video')
@require_api_key
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

@app.route('/test')
@require_api_key
def test_endpoint():
    """Test endpoint to verify API functionality"""
    try:
        return jsonify({
            'status': 'success',
            'message': 'API is working correctly',
            'timestamp': str(datetime.datetime.now()),
            'endpoints': [
                '/api/fast-meta',
                '/api/all', 
                '/download',
                '/api/audio',
                '/api/video'
            ]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

