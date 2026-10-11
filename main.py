import asyncio
from pathlib import Path
import pygame
import subprocess
import json
import os
import shlex
import traceback
import copy
import re
import shutil
import hashlib
import hmac
import secrets

# Optional audio mixer: app still starts on systems without an audio device.
try:
    pygame.mixer.init()
except pygame.error:
    pass
import zipfile
from datetime import datetime


# --- Integrated community gallery (no community_v17.py required) ---
"""Community gallery UI and local/optional-server publishing for 2d appmaker."""
import json, os, re, shutil, subprocess, sys, tempfile, zipfile
from pathlib import Path
from urllib import request, parse, error
import pygame
community_ROOT = (Path('/data') if sys.platform == 'emscripten' else (Path('/data') if sys.platform == 'emscripten' else Path.home() / '2d_appmaker_projects')) / 'community'
community_ROOT.mkdir(parents=True, exist_ok=True)
community_CONFIG = Path(__file__).with_name('community_config.json')
try:
    community_CONFIG_DATA = json.loads(community_CONFIG.read_text(encoding='utf8'))
except (OSError, ValueError):
    community_CONFIG_DATA = {}
community_SERVER = community_CONFIG_DATA.get('server_url', '').rstrip('/')
community_API_KEY = community_CONFIG_DATA.get('upload_key', '')
publish_modal = {'open': False, 'path': '', 'online': False, 'title': '', 'description': '', 'cover': '', 'field': 'title'}
community_state = {'page': 'gallery', 'search': '', 'focus': False, 'scope': 'local', 'notice': 'Browse published games or search for a creator.', 'scroll': 0, 'cards': [], 'actions': [], 'online_cache': [], 'online_loaded': False}

def community__slug(s):
    return re.sub('[^A-Za-z0-9_-]+', '_', s)[:55].strip('_') or 'game'

def community__get(url):
    with request.urlopen(url, timeout=5) as r:
        return r.read()

def community_local_games():
    result = []
    for meta in community_ROOT.glob('*/info.json'):
        try:
            info = json.loads(meta.read_text(encoding='utf8'))
            if (meta.parent / 'game.zip').exists():
                result.append(dict(info, local_dir=str(meta.parent), source='local'))
        except (OSError, ValueError):
            pass
    return sorted(result, key=lambda i: i.get('published', ''), reverse=True)

def community_refresh_online():
    if ONLINE_SETTINGS.get('url'):
        online_gallery_refresh()
        return
    if not community_SERVER:
        community_state['notice'] = 'Online setup needed. See SETUP_V20.md.'
        return
    try:
        result = json.loads(community__get(community_SERVER + '/api/games').decode('utf8'))
        community_state['online_cache'] = result.get('games', [])[:200]
        community_state['online_loaded'] = True
        community_state['notice'] = 'Online gallery refreshed.'
    except Exception as e:
        community_state['notice'] = 'Cannot connect to online server: ' + str(e)[:75]

def community_publish_local(project_path, username, title, exporter, description='', cover=''):
    exporter()
    source = Path(project_path).with_name(Path(project_path).stem + '_playable.zip')
    if not source.is_file():
        community_state['notice'] = 'Export missing. Check editor message and ensure player file is present.'
        return None
    target = community_ROOT / (community__slug(username) + '__' + community__slug(title))
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target / 'game.zip')
    from datetime import datetime, timezone
    if cover and Path(cover).is_file() and Path(cover).suffix.lower() in ('.png','.jpg','.jpeg'):
        try:
            shutil.copy2(cover,target / ('cover'+Path(cover).suffix.lower()))
            cover=str(target / ('cover'+Path(cover).suffix.lower()))
        except OSError:
            cover=''
    else:
        cover=''
    info = {'id': target.name, 'title': title, 'creator': username, 'description': description[:1200], 'cover': cover, 'project_file': str(Path(project_path).resolve()), 'published': datetime.now(timezone.utc).isoformat()}
    (target / 'info.json').write_text(json.dumps(info, indent=2), encoding='utf8')
    community_state['notice'] = 'Published locally! Open Play Others Games to test.'
    return target / 'game.zip'

def community_remove_local_project(project_path, username, title):
    """Remove all local publications associated with the deleted project."""
    project_path = str(Path(project_path).resolve())
    removed = 0
    for meta in community_ROOT.glob('*/info.json'):
        try:
            info = json.loads(meta.read_text(encoding='utf8'))
            match = (info.get('project_file') == project_path or
                     (not info.get('project_file') and info.get('creator') == username and
                      community__slug(info.get('title','')) == community__slug(title)))
            if match:
                shutil.rmtree(meta.parent)
                removed += 1
        except (OSError, ValueError):
            continue
    return removed

def community_publish_online(zip_path, username, title):
    if ONLINE_SETTINGS.get('url'):
        online_publish_game(zip_path,title)
        return
    if not community_SERVER:
        community_state['notice'] = 'Online setup needed. See SETUP_V20.md.'
        return
    if not community_API_KEY:
        community_state['notice'] = 'Online publishing requires an upload_key in community_config.json.'
        return
    try:
        payload = Path(zip_path).read_bytes()
        if len(payload) > 20000000:
            raise ValueError('Game exceeds 20 MB upload limit')
        req = request.Request(community_SERVER + '/api/publish?' + parse.urlencode({'title': title, 'creator': username}), payload, headers={'Content-Type': 'application/zip', 'X-Upload-Key': community_API_KEY}, method='POST')
        result = json.loads(request.urlopen(req, timeout=15).read())
        community_state['notice'] = 'Online published: ' + result.get('id', 'success')
        community_state['online_loaded'] = False
    except Exception as e:
        community_state['notice'] = 'Online publish failed: ' + str(e)[:85]

def community_play_game(card, player_file):
    try:
        if card['source'] == 'local':
            archive = Path(card['local_dir']) / 'game.zip'
        elif card['source'] == 'supabase':
            archive = online_download_game(card)
        else:
            game_id = card.get('id', '')
            if not re.fullmatch('[A-Za-z0-9_-]{1,100}', game_id):
                raise ValueError('Invalid game ID')
            payload = community__get(community_SERVER + '/api/games/' + game_id + '/download')
            if len(payload) > 20000000:
                raise ValueError('Download too large')
            archive = community_ROOT / ('_download_' + game_id + '.zip')
            archive.write_bytes(payload)
        player = Path(player_file)
        if not player.is_file():
            raise FileNotFoundError('Place 2d_appmaker_player_v14.py beside the editor.')
        folder = Path(tempfile.mkdtemp(prefix='appmaker_play_'))
        total = 0
        with zipfile.ZipFile(archive) as z:
            for item in z.infolist():
                name = item.filename
                if name == 'game.json' or (name.startswith('assets/') and (not name.endswith('/'))):
                    if item.file_size > 10000000:
                        raise ValueError('Asset too large')
                    total += item.file_size
                    if total > 40000000:
                        raise ValueError('Game too large')
                    destination = folder / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(z.read(item))
        if not (folder / 'game.json').exists():
            raise ValueError('Game data missing')
        shutil.copy2(player, folder / 'play_game.py')
        subprocess.Popen([sys.executable, str(folder / 'play_game.py')], cwd=folder)
        community_state['notice'] = 'Launched ' + card.get('title', 'game') + ' in a separate window.'
    except Exception as e:
        community_state['notice'] = 'Cannot play: ' + str(e)[:90]

def community__txt(screen, font, s, x, y, color, max_width=None):
    s = display_safe(s)
    font = interface_font(font, s)
    if max_width:
        while s and font.size(s)[0] > max_width:
            s = s[:-2] + '…'
    screen.blit(font.render(s, True, color), (x, y))

def community_draw(screen, fonts, colors, profile):
    font, small, big = fonts
    w, h = screen.get_size()
    pink = colors['accent']
    white = colors['text']
    muted = colors['muted']
    screen.fill(colors['bg'])
    community_state['actions'] = []
    community__txt(screen, big, '2d appmaker', 45, 28, pink)

    def btn(label, rect, action):
        pygame.draw.rect(screen, colors['selected'], rect, border_radius=8)
        pygame.draw.rect(screen, pink, rect, 2, border_radius=8)
        community__txt(screen, small, label, rect.x + 12, rect.y + (rect.h - 17) // 2, white, rect.w - 18)
        community_state['actions'].append((rect, action))
    btn(ui('Back to Projects'), pygame.Rect(w - 188, 30, 154, 38), ('back', None))
    community__txt(screen, big, ui('Community Games'), 45, 100, white)
    community__txt(screen, small, ui('Find games by creator username or title'), 45, 135, muted)
    search_rect = pygame.Rect(45, 173, w - 365, 43)
    pygame.draw.rect(screen, colors['panel'], search_rect, border_radius=7)
    pygame.draw.rect(screen, pink if community_state['focus'] else colors['line'], search_rect, 2, border_radius=7)
    community__txt(screen, font, community_state['search'] or ui('Search username or game title...'), search_rect.x + 12, search_rect.y + 10, white if community_state['search'] else muted, search_rect.w - 22)
    community_state['search_rect'] = search_rect
    btn(ui('Local Games'), pygame.Rect(w - 300, 174, 116, 40), ('scope', 'local'))
    btn(ui('Online Games'), pygame.Rect(w - 177, 174, 132, 40), ('scope', 'online'))
    if community_state['scope'] == 'online':
        btn(ui('Refresh online'), pygame.Rect(45, 230, 140, 35), ('refresh', None))
        if not community_SERVER and not ONLINE_SETTINGS.get('url'):
            community__txt(screen, small, ui('Set up Supabase to see public games.'), 200, 238, muted, w - 240)
    else:
        community__txt(screen, small, ui('Local gallery: games published on this Mac'), 45, 240, muted)
    cards = community_local_games() if community_state['scope'] == 'local' else community_state['online_cache']
    query = community_state['search'].casefold().strip()
    cards = [c for c in cards if query in (c.get('title', '') + ' ' + c.get('creator', '')).casefold()]
    community_state['cards'] = cards
    cols = 2 if w >= 920 else 1
    gap = 16
    margin = 45
    cardw = (w - 2 * margin - (cols - 1) * gap) // cols
    cardh = 184
    top = 290
    max_rows = max(1, (h - top - 80) // (cardh + gap))
    max_scroll = max(0, (len(cards) + cols - 1) // cols - max_rows)
    community_state['scroll'] = min(community_state['scroll'], max_scroll)
    old = screen.get_clip()
    screen.set_clip(pygame.Rect(30, top - 5, w - 60, h - top - 75))
    for idx, card in enumerate(cards[community_state['scroll'] * cols:(community_state['scroll'] + max_rows) * cols]):
        rect = pygame.Rect(margin + idx % cols * (cardw + gap), top + idx // cols * (cardh + gap), cardw, cardh)
        pygame.draw.rect(screen, colors['panel'], rect, border_radius=10)
        pygame.draw.rect(screen, colors['line'], rect, 2, border_radius=10)
        cover_file=card.get('cover','')
        cover_offset=0
        if cover_file and Path(cover_file).is_file():
            try:
                cover_img=pygame.image.load(cover_file)
                cover_img=pygame.transform.smoothscale(cover_img,(130,130))
                screen.blit(cover_img,(rect.x+10,rect.y+10))
                cover_offset=137
            except (pygame.error,OSError):pass
        community__txt(screen, font, card.get('title', 'Untitled'), rect.x + 15+cover_offset, rect.y + 15, white, rect.w - 30-cover_offset)
        community__txt(screen, small, ui('by') + ' ' + card.get('creator', 'unknown'), rect.x + 15+cover_offset, rect.y + 46, muted, rect.w - 30-cover_offset)
        community__txt(screen, small, card.get('description','')[:100], rect.x + 15+cover_offset, rect.y + 76, muted, rect.w - 30-cover_offset)
        btn(ui('Play Game'), pygame.Rect(rect.x + 15, rect.bottom - 45, 125, 32), ('play', card))
        if community_state['scope'] == 'local':
            label = ui('Confirm Delete') if community_state.get('delete_pending') == card.get('local_dir') else ui('Delete Game')
            btn(label, pygame.Rect(rect.right - 156, rect.bottom - 45, 140, 32), ('delete_local', card))
    screen.set_clip(old)
    if not cards:
        community__txt(screen, font, ui('No games found yet. Publish one from My Projects!'), 45, 310, muted, w - 90)
    if max_scroll:
        btn('Up', pygame.Rect(w - 158, h - 87, 65, 30), ('scroll', -1))
        btn('Down', pygame.Rect(w - 85, h - 87, 65, 30), ('scroll', 1))
    pygame.draw.rect(screen, colors['bg'], pygame.Rect(0, h - 47, w, 47))
    community__txt(screen, small, community_state['notice'], 45, h - 33, pink, w - 90)
    pygame.display.flip()

def community_click(pos, player_file):
    if community_state.get('search_rect', pygame.Rect(0, 0, 0, 0)).collidepoint(pos):
        community_state['focus'] = True
        return None
    community_state['focus'] = False
    for rect, (action, arg) in reversed(community_state['actions']):
        if rect.collidepoint(pos):
            if action == 'back':
                return 'home'
            if action == 'scope':
                community_state['scope'] = arg
                community_state['scroll'] = 0
                if arg=='online' and ONLINE_SETTINGS.get('url'):online_gallery_refresh()
            if action == 'refresh':
                community_refresh_online()
            if action == 'scroll':
                community_state['scroll'] = max(0, community_state['scroll'] + arg)
            if action == 'delete_local':
                folder = arg.get('local_dir', '')
                if community_state.get('delete_pending') != folder:
                    community_state['delete_pending'] = folder
                    community_state['notice'] = ui('Click Confirm Delete to permanently remove this local published game.')
                else:
                    try:
                        target = Path(folder).resolve()
                        if target.parent == community_ROOT.resolve() and (target / 'info.json').is_file():
                            shutil.rmtree(target)
                            community_state['notice'] = ui('Local published game deleted.')
                        else:
                            community_state['notice'] = 'Cannot delete: invalid gallery folder.'
                    except OSError as exc:
                        community_state['notice'] = 'Delete failed: ' + str(exc)[:80]
                    community_state['delete_pending'] = None
            if action == 'play':
                community_play_game(arg, player_file)
            return None
    return None

def community_key(event):
    if not community_state['focus']:
        return
    if event.key == pygame.K_BACKSPACE:
        community_state['search'] = community_state['search'][:-1]
    elif event.key == pygame.K_ESCAPE:
        community_state['focus'] = False
    elif event.unicode and event.unicode.isprintable() and (len(community_state['search']) < 100):
        community_state['search'] += event.unicode



# === Supabase online accounts and shared game gallery (v20) ===
# Client contains ONLY the publishable/anon key; never put service_role keys here.
ONLINE_CONFIG_PATH = Path(__file__).with_name('supabase_config.json')
try:
    ONLINE_SETTINGS = json.loads(ONLINE_CONFIG_PATH.read_text(encoding='utf8'))
except (OSError, ValueError):
    ONLINE_SETTINGS = {}
ONLINE_CLIENT = None
ONLINE_USER = None
ONLINE_USERNAME = ''
ONLINE_MODE = False
ONLINE_AUTH_SCREEN = False
ONLINE_AUTH_ACTION = 'sign_in'
ONLINE_AUTH_FOCUS = 'username'
ONLINE_FORM = {'username':'', 'email':'', 'password':''}
ONLINE_NOTICE = ''
ONLINE_REMEMBER_PATH = (Path('/data') if sys.platform == 'emscripten' else (Path('/data') if sys.platform == 'emscripten' else Path.home() / '2d_appmaker_projects')) / 'remembered_online_login.json'
try:
    ONLINE_REMEMBERED = json.loads(ONLINE_REMEMBER_PATH.read_text(encoding='utf-8'))
    if not isinstance(ONLINE_REMEMBERED, dict): ONLINE_REMEMBERED = {}
except (OSError, ValueError):
    ONLINE_REMEMBERED = {}
ONLINE_REMEMBER_ENABLED = bool(ONLINE_REMEMBERED.get('remember', True))
ONLINE_FORM['email'] = str(ONLINE_REMEMBERED.get('email', ''))[:128]

# Refresh tokens are stored in the macOS Keychain through keyring, never in JSON.
ONLINE_KEYRING_SERVICE = '2d_appmaker.supabase.session'
ONLINE_RESTORE_ATTEMPTED = False

def online_keyring():
    try:
        import keyring
        return keyring
    except ImportError:
        return None

def online_session_key():
    # Different Supabase projects cannot share refresh tokens.
    url = str(ONLINE_SETTINGS.get('url', '')).strip().rstrip('/')
    return hashlib.sha256(url.encode('utf-8')).hexdigest() if url else ''

def online_store_session(session):
    if not ONLINE_REMEMBER_ENABLED or session is None:
        return
    token = getattr(session, 'refresh_token', None)
    key = online_session_key()
    vault = online_keyring()
    if token and key and vault:
        try:
            vault.set_password(ONLINE_KEYRING_SERVICE, key, token)
        except Exception:
            pass  # Keychain unavailable; sign-in still works for this run.

def online_forget_session():
    key = online_session_key()
    vault = online_keyring()
    if key and vault:
        try:
            vault.delete_password(ONLINE_KEYRING_SERVICE, key)
        except Exception:
            pass

def online_restore_session():
    global ONLINE_RESTORE_ATTEMPTED, ONLINE_NOTICE
    if ONLINE_RESTORE_ATTEMPTED:
        return
    ONLINE_RESTORE_ATTEMPTED = True
    if not ONLINE_REMEMBER_ENABLED or not ONLINE_SETTINGS.get('url'):
        return
    vault = online_keyring()
    key = online_session_key()
    if not vault or not key:
        return
    try:
        token = vault.get_password(ONLINE_KEYRING_SERVICE, key)
        if not token:
            return
        client = online_client()
        response = client.auth.refresh_session(token)
        if not response.user or not response.session:
            raise RuntimeError('Session expired')
        online_enter_account(response.user)
        online_store_session(response.session)  # Persist rotated refresh token.
    except Exception:
        online_forget_session()
        ONLINE_NOTICE = 'Please sign in again.'

def online_save_remembered(success=False):
    # Only email and login timestamp are saved; never a password or auth token.
    global ONLINE_REMEMBERED
    if not ONLINE_REMEMBER_ENABLED:
        ONLINE_REMEMBERED = {'remember': False}
    else:
        ONLINE_REMEMBERED = {'remember': True, 'email': ONLINE_FORM['email'].strip()}
        if success:
            ONLINE_REMEMBERED['last_login'] = datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')
        elif ONLINE_REMEMBERED.get('email') == ONLINE_FORM['email'].strip():
            old = getattr(online_save_remembered, '_last_login', None)
            if old: ONLINE_REMEMBERED['last_login'] = old
    try:
        ONLINE_REMEMBER_PATH.parent.mkdir(parents=True, exist_ok=True)
        ONLINE_REMEMBER_PATH.write_text(json.dumps(ONLINE_REMEMBERED, indent=2), encoding='utf-8')
    except OSError:
        pass
online_save_remembered._last_login = ONLINE_REMEMBERED.get('last_login')


def online_client():
    global ONLINE_CLIENT
    if ONLINE_CLIENT is not None: return ONLINE_CLIENT
    url = ONLINE_SETTINGS.get('url','').strip()
    key = ONLINE_SETTINGS.get('publishable_key','').strip()
    if not url.startswith('https://') or not key:
        raise ValueError('Configure url and publishable_key in supabase_config.json')
    try:
        from supabase import create_client
    except ImportError:
        raise RuntimeError('Install Supabase in PyCharm Terminal: python -m pip install supabase')
    ONLINE_CLIENT = create_client(url, key)
    return ONLINE_CLIENT

def online_enter_account(user):
    global ONLINE_USER, ONLINE_USERNAME, ONLINE_MODE, ONLINE_AUTH_SCREEN
    global profile_slug, profile_name, app_page, home_notice, home_input_mode
    client = online_client()
    # The user id is provided by Supabase Auth; the profile is read under RLS.
    row = client.table('profiles').select('username').eq('id',str(user.id)).single().execute().data
    if not row or not row.get('username'):
        raise RuntimeError('Online profile not ready. Verify your email and sign in again.')
    ONLINE_USER = user
    ONLINE_USERNAME = row['username']
    ONLINE_MODE = True
    ONLINE_AUTH_SCREEN = False
    profile_slug = 'online_' + str(user.id).replace('-','')
    profile_name = ONLINE_USERNAME
    Path(profile_path(profile_slug),'projects').mkdir(parents=True,exist_ok=True)
    home_input_mode='project'
    app_page='home'
    home_notice='Online account: @' + ONLINE_USERNAME + ' | Projects remain on this Mac.'
    online_save_remembered(success=True)
    online_save_remembered._last_login = ONLINE_REMEMBERED.get('last_login')

def online_submit():
    global ONLINE_NOTICE, ONLINE_AUTH_ACTION
    username = ONLINE_FORM['username'].strip()
    email = ONLINE_FORM['email'].strip()
    password = ONLINE_FORM['password']
    try:
        client = online_client()
        if ONLINE_AUTH_ACTION == 'sign_up':
            if not re.fullmatch(r'[A-Za-z0-9_]{3,24}',username):
                raise ValueError('Username: 3-24 letters, numbers or underscores.')
            if len(password)<8: raise ValueError('Password must be at least 8 characters.')
            response = client.auth.sign_up({'email':email,'password':password,
                'options':{'data':{'username':username}}})
            if response.session and response.user:
                online_enter_account(response.user)
                online_store_session(response.session)
            else:
                ONLINE_NOTICE='Check your email!'
        else:
            response = client.auth.sign_in_with_password({'email':email,'password':password})
            if not response.user: raise RuntimeError('Sign in failed.')
            online_enter_account(response.user)
            online_store_session(response.session)
    except Exception as exc:
        ONLINE_NOTICE='Online: '+str(exc)[:115]
    else:
        ONLINE_FORM['password']=''

def online_sign_out():
    online_forget_session()
    global ONLINE_USER, ONLINE_MODE, ONLINE_USERNAME, ONLINE_AUTH_SCREEN, ONLINE_NOTICE
    try:
        if ONLINE_CLIENT: ONLINE_CLIENT.auth.sign_out()
    except Exception: pass
    ONLINE_USER=None; ONLINE_MODE=False; ONLINE_USERNAME=''
    ONLINE_AUTH_SCREEN=False
    ONLINE_NOTICE='Signed out of online account.'

def online_gallery_refresh():
    try:
        data = online_client().table('published_games').select('id,title,creator,storage_path,published').order('published',desc=True).limit(200).execute().data
        community_state['online_cache'] = [dict(x,source='supabase') for x in (data or [])]
        community_state['notice']='Loaded public games from Supabase.'
    except Exception as exc:
        community_state['notice']='Online gallery: '+str(exc)[:95]

def online_publish_game(zip_path, title):
    if not ONLINE_MODE or not ONLINE_USER:
        community_state['notice']='Sign in with an Online Account to publish online.'
        return
    try:
        archive = Path(zip_path)
        if not archive.exists(): raise ValueError('First publish locally to create the ZIP.')
        if archive.stat().st_size > 15*1024*1024: raise ValueError('Game must be 15 MB or smaller.')
        # Validate the archive is data-only and has a game.json file.
        with zipfile.ZipFile(archive) as z:
            names=z.namelist()
            if 'game.json' not in names or any(n.startswith('/') or '..' in Path(n).parts for n in names):
                raise ValueError('Invalid game archive.')
            if any(n != 'game.json' and n != 'play_game.py' and not n.startswith('assets/') and n != 'README.txt' for n in names):
                raise ValueError('Archive contains unsupported files.')
        client=online_client()
        from uuid import uuid4
        game_id=str(uuid4())
        path=str(ONLINE_USER.id)+'/'+game_id+'.zip'
        client.storage.from_('published-games').upload(path,archive.read_bytes(),{'content-type':'application/zip'})
        try:
            client.table('published_games').insert({'id':game_id,'owner_id':str(ONLINE_USER.id),
                'title':title[:80], 'storage_path':path}).execute()
        except Exception:
            client.storage.from_('published-games').remove([path])
            raise
        community_state['notice']='Published online! Other people can now find your game.'
        online_gallery_refresh()
    except Exception as exc:
        community_state['notice']='Online publish failed: '+str(exc)[:95]

def online_download_game(card):
    path=card.get('storage_path','')
    # Read is allowed only for published game archives by storage RLS.
    if not re.fullmatch(r'[0-9a-f-]{36}/[0-9a-f-]{36}\.zip',path):
        raise ValueError('Invalid online game path.')
    data=online_client().storage.from_('published-games').download(path)
    if len(data)>15*1024*1024:raise ValueError('Online game is too large.')
    target=community_ROOT / ('_supabase_'+card['id']+'.zip')
    target.write_bytes(data)
    return target

def online_draw_auth():
    screen.fill(COL['bg'])
    w,h=screen.get_size(); x=w//2-275
    pygame.draw.rect(screen,COL['panel'],(x,110,550,590),border_radius=14)
    pygame.draw.rect(screen,COL['line'],(x,110,550,590),2,border_radius=14)
    draw_text('Online Creator Account',x+25,137,COL['accent'],BIG)
    draw_text('Share games with creators on other computers.',x+25,179,COL['muted'],SMALL)
    online_auth_rects.clear()
    for label,action,xx in [('Sign In','sign_in',x+25),('Create Account','sign_up',x+167)]:
        r=pygame.Rect(xx,218,132 if action=='sign_in' else 166,37)
        online_auth_rects[action]=r
        pygame.draw.rect(screen,COL['accent'] if ONLINE_AUTH_ACTION==action else COL['selected'],r,border_radius=8)
        draw_text(label,r.x+12,r.y+9,COL['bg'] if ONLINE_AUTH_ACTION==action else COL['text'],SMALL)
    y=290
    fields=['username','email','password'] if ONLINE_AUTH_ACTION=='sign_up' else ['email','password']
    for field in fields:
        draw_text(field.title(),x+25,y-20,COL['muted'],SMALL)
        r=pygame.Rect(x+25,y,500,44);online_auth_rects[field]=r
        pygame.draw.rect(screen,COL['bg'],r,border_radius=7)
        pygame.draw.rect(screen,COL['accent'] if ONLINE_AUTH_FOCUS==field else COL['line'],r,2,border_radius=7)
        val=ONLINE_FORM[field]
        if field=='password':val='*'*len(val)
        draw_text(val[-46:] or ('Enter '+field),r.x+12,r.y+12,COL['text'] if val else COL['muted'],SMALL)
        y+=83
    submit=pygame.Rect(x+25,y+6,240,42);online_auth_rects['submit']=submit
    button(submit,'Create Account' if ONLINE_AUTH_ACTION=='sign_up' else 'Sign In',small=True)
    back=pygame.Rect(x+290,y+6,235,42);online_auth_rects['back']=back
    button(back,'Back to Local Login',small=True)
    remember=pygame.Rect(x+25,y+62,25,25);online_auth_rects['remember']=remember
    pygame.draw.rect(screen,COL['accent'] if ONLINE_REMEMBER_ENABLED else COL['bg'],remember,border_radius=5)
    pygame.draw.rect(screen,COL['line'],remember,2,border_radius=5)
    draw_text('Remember email on this Mac',x+58,y+64,COL['text'],SMALL)
    last=ONLINE_REMEMBERED.get('last_login')
    if last:draw_text('Last successful login: '+str(last)[:30],x+25,y+98,COL['muted'],SMALL)
    draw_text(ONLINE_NOTICE[:77],x+25,660,COL['accent'],SMALL)
    pygame.display.flip()

online_auth_rects={}
def online_auth_click(pos):
    global ONLINE_AUTH_ACTION, ONLINE_AUTH_FOCUS, ONLINE_AUTH_SCREEN, ONLINE_NOTICE, ONLINE_REMEMBER_ENABLED
    for key,rect in online_auth_rects.items():
        if not rect.collidepoint(pos):continue
        if key in ('sign_in','sign_up'):
            ONLINE_AUTH_ACTION=key;ONLINE_AUTH_FOCUS='username' if key=='sign_up' else 'email'
        elif key in ONLINE_FORM: ONLINE_AUTH_FOCUS=key
        elif key=='remember':
            ONLINE_REMEMBER_ENABLED=not ONLINE_REMEMBER_ENABLED
            if not ONLINE_REMEMBER_ENABLED: online_forget_session()
            online_save_remembered()
        elif key=='submit': online_submit()
        elif key=='back': ONLINE_AUTH_SCREEN=False
        return

def online_auth_key(event):
    global ONLINE_AUTH_FOCUS
    fields=['username','email','password'] if ONLINE_AUTH_ACTION=='sign_up' else ['email','password']
    if event.key==pygame.K_TAB:
        ONLINE_AUTH_FOCUS=fields[(fields.index(ONLINE_AUTH_FOCUS)+1)%len(fields)] if ONLINE_AUTH_FOCUS in fields else fields[0]
    elif event.key==pygame.K_RETURN:
        idx=fields.index(ONLINE_AUTH_FOCUS) if ONLINE_AUTH_FOCUS in fields else len(fields)-1
        if idx==len(fields)-1:online_submit()
        else:ONLINE_AUTH_FOCUS=fields[idx+1]
    elif event.key==pygame.K_BACKSPACE:
        ONLINE_FORM[ONLINE_AUTH_FOCUS]=ONLINE_FORM[ONLINE_AUTH_FOCUS][:-1]
    elif event.unicode and event.unicode.isprintable() and len(ONLINE_FORM[ONLINE_AUTH_FOCUS])<128:
        ONLINE_FORM[ONLINE_AUTH_FOCUS]+=event.unicode


# The trusted, data-only game player is bundled into this one app file.
BUNDLED_PLAYER_SOURCE = '"""Standalone, data-only 2d appmaker game player. No project Python is executed."""\nfrom pathlib import Path\nimport json\nimport pygame\nimport sys\n\nROOT=Path(getattr(sys,\'_MEIPASS\',Path(__file__).resolve().parent))\npygame.init()\nscreen=pygame.display.set_mode((960,640),pygame.RESIZABLE)\nclock=pygame.time.Clock()\nfont=pygame.font.SysFont(\'Arial\',23)\nsmall=pygame.font.SysFont(\'Arial\',16)\nPINK=(245,163,201);WHITE=(255,236,245);DARK=(43,38,48)\ntry:\n    data=json.loads((ROOT/\'game.json\').read_text(encoding=\'utf-8\'))\nexcept Exception as e:\n    raise SystemExit(f\'Could not load game.json: {e}\')\nscenes=data.get(\'scenes\',{})\norder=data.get(\'scene_order\',list(scenes))\nif not scenes:raise SystemExit(\'This export has no scenes\')\nscene_name=order[0] if order and order[0] in scenes else next(iter(scenes))\ncache={}\nline_idx=0\nactive_animations={}\nplayer_pos={}\ncamera=[0.0,0.0]\n\ndef image(path):\n    if not path:return None\n    if path not in cache:\n        try:\n            p=(ROOT/path).resolve()\n            if not p.is_relative_to(ROOT):return None\n            cache[path]=pygame.image.load(str(p)).convert_alpha()\n        except (OSError,pygame.error,ValueError):cache[path]=None\n    return cache[path]\n\ndef text(value,x,y,face=font,color=WHITE,max_width=None):\n    value=str(value)\n    if max_width:\n        words=value.split();rows=[];row=\'\'\n        for word in words:\n            trial=(row+\' \'+word).strip()\n            if row and face.size(trial)[0]>max_width:rows.append(row);row=word\n            else:row=trial\n        if row:rows.append(row)\n    else:rows=[value]\n    for part in rows[:5]:\n        screen.blit(face.render(part,True,color),(x,y));y+=face.get_linesize()+3\n\ndef enter(name):\n    global scene_name,line_idx\n    camera[:]=[0.0,0.0]\n    if name in scenes:\n        scene_name=name;line_idx=0\n        pygame.display.set_caption(\'2d appmaker | \'+data.get(\'project_name\',\'Game\')+\' | \'+name)\n\ndef geometry():\n    w,h=screen.get_size()\n    return pygame.Rect(12,12,max(100,w-24),max(100,h-24))\n\ndef items():return scenes[scene_name].get(\'items\',[])\n\ndef get_player():\n    s=scenes[scene_name];pid=s.get(\'player_id\')\n    return next((i for i in items() if i.get(\'id\')==pid and i.get(\'kind\')==\'character\'),None) or next((i for i in items() if i.get(\'kind\')==\'character\'),None)\n\ndef get_rect(item,area):\n    x,y=player_pos.get((scene_name,item.get(\'id\')),(item.get(\'x\',0),item.get(\'y\',0)))\n    return pygame.Rect(area.x+int(x-camera[0]),area.y+int(y-camera[1]),max(1,int(item.get(\'w\',64))),max(1,int(item.get(\'h\',64))))\n\ndef move(dx,dy):\n    s=scenes[scene_name]\n    if s.get(\'type\')!=\'exploration\' or not s.get(\'movement\',True):return\n    player=get_player()\n    if not player:return\n    area=geometry();old=get_rect(player,area).move(int(camera[0]),int(camera[1]))\n    for mx,my in ((dx,0),(0,dy)):\n        target=old.move(mx,my)\n        bounds=pygame.Rect(area.x,area.y,int(s.get("map_width",area.w)),int(s.get("map_height",area.h)))\n        if not bounds.contains(target):continue\n        if s.get(\'collision\',True) and any(i is not player and i.get(\'kind\')==\'object\' and i.get(\'visible\',True) and target.colliderect(get_rect(i,area).move(int(camera[0]),int(camera[1]))) for i in items()):continue\n        old=target\n    player_pos[(scene_name,player.get(\'id\'))]=(old.x-area.x,old.y-area.y)\n\ndef choice_rects():\n    s=scenes[scene_name];choices=s.get(\'choices\',[])\n    if s.get(\'type\')!=\'choice\':return []\n    w,h=screen.get_size();cw=min(600,w-70);x=(w-cw)//2\n    return [(pygame.Rect(x,110+j*55,cw,46),ch) for j,ch in enumerate(choices[:8])]\n\ndef play_clip(item,name,op=\'PLAY\'):\n    clips=item.get(\'animations\',{})\n    if name not in clips or not clips[name].get(\'frames\'):return\n    key=(scene_name,item.get(\'id\'))\n    if op==\'TOGGLE\' and key in active_animations and active_animations[key][0]==name:\n        active_animations.pop(key,None)\n    else:active_animations[key]=(name,pygame.time.get_ticks())\n\ndef draw():\n    screen.fill(DARK);area=geometry();s=scenes[scene_name]\n    player=get_player()\n    if player:\n        px,py=player_pos.get((scene_name,player.get(\'id\')),(player.get(\'x\',0),player.get(\'y\',0)))\n        maxx=max(0,int(s.get(\'map_width\',area.w))-area.w);maxy=max(0,int(s.get(\'map_height\',area.h))-area.h)\n        tx=max(0,min(maxx,px+player.get(\'w\',64)/2-area.w/2))\n        ty=max(0,min(maxy,py+player.get(\'h\',64)/2-area.h/2))\n        camera[0]+=(tx-camera[0])*0.14;camera[1]+=(ty-camera[1])*0.14\n    pygame.draw.rect(screen,(86,80,94),area)\n    bg=s.get(\'background\');im=image(bg)\n    if im:\n        r=s.get(\'bg_rect\',[0,0,area.w,area.h]);ww=max(1,int(r[2]));hh=max(1,int(r[3]))\n        screen.blit(pygame.transform.smoothscale(im,(ww,hh)),(area.x+int(r[0]-camera[0]),area.y+int(r[1]-camera[1])))\n    oldclip=screen.get_clip();screen.set_clip(area)\n    for i in items():\n        if not i.get(\'visible\',True):continue\n        r=get_rect(i,area);im=image(i.get(\'path\'))\n        playing=active_animations.get((scene_name,i.get(\'id\')))\n        if playing:\n            clip=i.get(\'animations\',{}).get(playing[0],{})\n            frames=clip.get(\'frames\',[])\n            if frames:\n                fps=max(1,min(60,int(clip.get(\'fps\',8))))\n                index=int((pygame.time.get_ticks()-playing[1])*fps/1000)\n                if not clip.get(\'loop\',True) and index>=len(frames):\n                    active_animations.pop((scene_name,i.get(\'id\')),None);index=len(frames)-1\n                im=image(frames[index%len(frames)]) or im\n        if im:screen.blit(pygame.transform.smoothscale(im,r.size),r)\n        elif i.get(\'kind\')==\'text\':text(i.get(\'text\',\'\'),r.x,r.y,max_width=max(100,area.w-r.x))\n        elif i.get(\'kind\')==\'button\':\n            pygame.draw.rect(screen,PINK,r,border_radius=7)\n            text(i.get(\'text\',\'Button\'),r.x+8,r.y+6,color=DARK)\n        else:pygame.draw.rect(screen,PINK,r,2)\n    screen.set_clip(oldclip)\n    lines=s.get(\'dialogue_lines\',[])\n    dialogue=lines[min(line_idx,len(lines)-1)] if lines and line_idx<len(lines) else (s.get(\'dialogue\',\'\') if not lines else \'\')\n    if dialogue:\n        style=s.get(\'dialogue_style\',{})\n        def color(value,fallback):\n            try:return tuple(bytes.fromhex(str(value).lstrip(\'#\')))\n            except (ValueError,TypeError):return fallback\n        height=max(60,min(250,int(style.get(\'height\',108))))\n        box=pygame.Rect(area.x+15,area.bottom-height-17,area.w-30,height)\n        layer=pygame.Surface(box.size,pygame.SRCALPHA)\n        pygame.draw.rect(layer,(*color(style.get(\'bg\'),DARK),max(0,min(255,int(style.get(\'opacity\',235))))),layer.get_rect(),border_radius=9)\n        screen.blit(layer,box.topleft)\n        pygame.draw.rect(screen,color(style.get(\'border\'),PINK),box,2,border_radius=9)\n        text(dialogue,box.x+15,box.y+14,color=color(style.get(\'text\'),WHITE),max_width=box.w-30)\n        text(\'Click or Space to continue\',box.x+15,box.bottom-24,small,PINK)\n    for r,ch in choice_rects():\n        pygame.draw.rect(screen,DARK,r,border_radius=8)\n        pygame.draw.rect(screen,PINK,r,2,border_radius=8)\n        text(ch.get(\'label\',\'Choice\'),r.x+12,r.y+10,max_width=r.w-20)\n    pygame.display.flip()\n\n\n# V21 safe, data-only event commands. Never eval/exec scripts from projects.\nimport random as _rpg_random\n\n\n# V64: click-to-advance, randomized NPC conversations.\n_npc_dialogue_queue = {}\n\ndef npc_dialogue_groups(source):\n    """Read RANDOM DIALOGUE / CHOICE / SAY / END RANDOM DIALOGUE blocks."""\n    groups=[];current=None;inside=False\n    for raw in source:\n        line=str(raw).strip()\n        upper=line.upper()\n        if upper==\'RANDOM DIALOGUE\':inside=True;continue\n        if upper==\'END RANDOM DIALOGUE\':\n            if current:groups.append(current)\n            inside=False;current=None;continue\n        if not inside:continue\n        if upper==\'CHOICE\':\n            if current:groups.append(current)\n            current=[]\n        elif upper.startswith(\'SAY \'):\n            if current is None:current=[]\n            current.append(line[4:].strip())\n    return groups\n\ndef npc_dialogue_step(owner, event, lines, say, scene_key):\n    if event.lower()!=\'click\':return False\n    groups=npc_dialogue_groups(lines)\n    if not groups:return False\n    key=(scene_key,owner.get(\'id\',id(owner)))\n    queue=_npc_dialogue_queue.get(key)\n    if not queue:\n        queue=list(_rpg_random.choice(groups))\n    say(queue.pop(0))\n    if queue:_npc_dialogue_queue[key]=queue\n    else:_npc_dialogue_queue.pop(key,None)\n    return True\n\ndef rpg_run(owner, event, objects, say, scene_change=None, flags=None, depth=0, lines_override=None):\n    if depth>8:return\n    scripts=owner.get(\'events\',{})\n    lines=scripts.get(event.lower(),[])\n    if isinstance(lines,str):lines=lines.splitlines()\n    if npc_dialogue_step(owner,event,lines,say,scene_name):return\n    for line in list(lines)[:80]:\n        line=str(line).strip()\n        if not line or line.startswith(\'#\'):continue\n        try:\n            import shlex\n            args=shlex.split(line)\n            if not args:continue\n            op=args[0].upper()\n            if op==\'CHANCE\':\n                chance=max(0,min(100,float(args[1])))\n                if _rpg_random.random()*100<chance:\n                    rpg_run({\'events\':{\'trigger\':[\' \'.join(args[2:])]}},\'trigger\',objects,say,scene_change,flags,depth+1)\n            elif op==\'SAY\':say(\' \'.join(args[1:]))\n            elif op in (\'PLAY\',\'TOGGLE\',\'STOP\') and len(args)>1 and args[1].upper()==\'ANIMATION\':\n                if op==\'STOP\':active_animations.pop((scene_name,owner.get(\'id\')),None)\n                elif len(args)>2:play_clip(owner,\' \'.join(args[2:]),op)\n            elif op in (\'HIDE\',\'DISAPPEAR\',\'KILL\'):owner[\'visible\']=False\n            elif op==\'SHOW\':owner[\'visible\']=True\n            elif op==\'MOVE\' and len(args)>=3:\n                directions={\'RIGHT\':(1,0),\'LEFT\':(-1,0),\'UP\':(0,-1),\'DOWN\':(0,1)}\n                dx,dy=directions[args[1].upper()];distance=max(-2000,min(2000,int(args[2])))\n                owner[\'x\']=owner.get(\'x\',0)+dx*distance;owner[\'y\']=owner.get(\'y\',0)+dy*distance\n            elif op==\'HEALTH\' and len(args)>1:owner[\'health\']=max(0,int(args[1]))\n            elif op==\'DAMAGE\' and len(args)>1:\n                owner[\'health\']=max(0,int(owner.get(\'health\',100))-max(0,int(args[1])))\n                if owner[\'health\']==0:rpg_run(owner,\'death\',objects,say,scene_change,flags,depth+1)\n            elif op==\'HEAL\' and len(args)>1:owner[\'health\']=int(owner.get(\'health\',100))+max(0,int(args[1]))\n            elif op==\'SET\' and len(args)>2 and flags is not None:flags[args[1]]=\' \'.join(args[2:])\n            elif op==\'IF\' and len(args)>4 and flags is not None and flags.get(args[1])==args[2]:\n                rpg_run({\'events\':{\'trigger\':[\' \'.join(args[3:])]}},\'trigger\',objects,say,scene_change,flags,depth+1)\n            elif op==\'SCENE\' and scene_change:scene_change(\' \'.join(args[1:]))\n            elif op==\'SPAWN\' and len(args)>1:\n                template=next((x for x in objects if x.get(\'name\',\'\').lower()==args[1].lower()),None)\n                if template:template[\'visible\']=True\n        except (ValueError,IndexError,KeyError,TypeError):continue\n\nrpg_flags={}\ndef rpg_say(value):\n    global line_idx\n    scenes[scene_name][\'dialogue\']=str(value);line_idx=0\ndef rpg_start():\n    sc=scenes[scene_name]\n    rpg_run(sc,\'start\',sc.get(\'items\',[]),rpg_say,enter,rpg_flags)\n    for obj in list(sc.get(\'items\',[])):\n        rpg_run(obj,\'start\',sc.get(\'items\',[]),rpg_say,enter,rpg_flags)\nenter(scene_name)\nrpg_start()\nrunning=True\nwhile running:\n    for event in pygame.event.get():\n        if event.type==pygame.QUIT:running=False\n        elif event.type==pygame.VIDEORESIZE:screen=pygame.display.set_mode((max(500,event.w),max(350,event.h)),pygame.RESIZABLE)\n        elif event.type==pygame.KEYDOWN:\n            keys={pygame.K_w:(0,-12),pygame.K_UP:(0,-12),pygame.K_s:(0,12),pygame.K_DOWN:(0,12),pygame.K_a:(-12,0),pygame.K_LEFT:(-12,0),pygame.K_d:(12,0),pygame.K_RIGHT:(12,0)}\n            if event.key in keys:move(*keys[event.key])\n            elif event.key in (pygame.K_SPACE,pygame.K_RETURN):\n                if _npc_dialogue_queue:\n                    for (sc,oid) in list(_npc_dialogue_queue):\n                        if sc==scene_name:\n                            obj=next((i for i in items() if i.get(\'id\')==oid),None)\n                            if obj:rpg_run(obj,\'click\',items(),rpg_say,enter,rpg_flags)\n                            break\n                else:line_idx+=1\n            elif event.key==pygame.K_ESCAPE:running=False\n        elif event.type==pygame.MOUSEBUTTONDOWN and event.button==1:\n            matched=False\n            for r,ch in choice_rects():\n                if r.collidepoint(event.pos):\n                    enter(ch.get(\'target\',ch.get(\'scene\',\'\')));rpg_start();matched=True;break\n            if not matched:\n                area=geometry()\n                for obj in reversed(items()):\n                    if obj.get(\'visible\',True) and get_rect(obj,area).collidepoint(event.pos):\n                        rpg_run(obj,\'click\',items(),rpg_say,enter,rpg_flags)\n                        break\n                else:\n                    rpg_run(scenes[scene_name],\'click\',items(),rpg_say,enter,rpg_flags)\n                if not _npc_dialogue_queue:line_idx+=1\n    # Timer events fire once per second; CHANCE commands implement random spawns.\n    if pygame.time.get_ticks()//1000 != globals().get(\'_rpg_last_second\',-1):\n        _rpg_last_second=pygame.time.get_ticks()//1000\n        rpg_run(scenes[scene_name],\'tick\',items(),rpg_say,enter,rpg_flags)\n        for obj in list(items()):rpg_run(obj,\'tick\',items(),rpg_say,enter,rpg_flags)\n    draw();clock.tick(60)\npygame.quit()\n'

def bundled_player_path():
    target = (Path('/data') if sys.platform == 'emscripten' else Path.home() / '2d_appmaker_projects') / '_trusted_player_v18.py'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or target.read_text(encoding='utf8') != BUNDLED_PLAYER_SOURCE:
        target.write_text(BUNDLED_PLAYER_SOURCE, encoding='utf8')
    return target

pygame.init()
W, H = 1280, 860
screen = pygame.display.set_mode((W, H), pygame.RESIZABLE)
pygame.display.set_caption('2d appmaker - Creator v34')
clock = pygame.time.Clock()
FONT = pygame.font.SysFont('Arial', 17)
SMALL = pygame.font.SysFont('Arial', 14)
BIG = pygame.font.SysFont('Arial', 24, bold=True)
COL={'bg':(45,43,51),'panel':(66,61,73),'canvas':(86,82,94),'toolbar':(66,61,73),'line':(155,117,143),'text':(255,238,246),'muted':(225,191,209),'accent':(246,143,188),'selected':(130,76,112),'green':(255,169,205),'red':(255,128,155)}
items=[]
selected_id=None
next_id=1
background=None
bg_path=None
bg_rect=[0,0,800,600]
active_panel='Code Chat'
entity_drawer_open=False
entity_scroll=0
entity_folders=[]
entity_assignments={}
audio_assignments={}
entity_folder_closed=set()
entity_folder_input=False
entity_folder_buffer=''
entity_move_menu=False
entity_drawer_pos=None
entity_drawer_dragging=False
entity_drawer_drag_offset=(0,0)
entity_row_drag=None  # pending/active drag of an entity or audio row
entity_row_drop_target=None
entity_rename_open=False
entity_rename_buffer=''
entity_quick_menu=False
selected_drawer_sound=None  # selected imported audio in Scene Entities
entity_code_open=False
entity_code_lines=[]
entity_code_row=0
entity_code_scroll=0
entity_code_target=None
mode='edit'
# V57: world map, independent editor view and play camera.
map_width=3000
map_height=2000
editor_zoom=1.0
editor_pan=[0.0,0.0]
map_panning=False
map_pan_start=(0,0)
map_pan_origin=(0.0,0.0)
play_camera=[0.0,0.0]
input_text=''
input_active=False
message='Welcome! Import images, then try MOVE Character 1 RIGHT 50'
chat_history=[]
dragging=False
drag_offset=(0,0)
resize_drag=False
file_menu=False
edit_menu=False
animation_play=False
animation_name_input=False
animation_name_buffer=''
frame_index=0
frame_tick=0
story_flags={}
dialogue=''
# v22: typewriter speed in characters per second; 0 disables it.
typewriter_cps=38
dialogue_started_ms=0
console_scroll=0
manual_scroll=0
manual_scroll_x=0
manual_hdrag=False
settings_open=False
ui_language='English'
video_path=None
script_lines=['# Python script: use api.move(1, 40, 0)', 'api.say("Hello from Python!")']
script_row=0
script_col=0
script_scroll=0
script_confirm=False
manual_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'coding_manual.txt')

# SCENES: Each scene owns its own background, items, and story settings.
# Asset images are reloaded from their original paths when switching scenes.
scenes={}
scene_order=['Scene 1']
current_scene='Scene 1'
scene_type='exploration'
movement_enabled=True
player_id=None
scene_choices=[]
choice_open=False
scene_name_input=False
scene_name_buffer=''
scene_status='Create scenes and switch between exploration, dialogue, and choices.'
scene_dialogue=''
scene_events={}
dialogue_style={'bg':'#191C2A','border':'#F5A3C9','text':'#FFFFFF','opacity':235,'height':90}
dialogue_lines=[]
dialogue_index=0
collision_enabled=True
branch_scroll=0

# Local creator profiles (no online login or passwords).
DATA_DIR=os.path.join(os.path.expanduser('~'), '2d_appmaker_projects')
PROFILE_DIR=os.path.join(DATA_DIR, 'profiles')
os.makedirs(PROFILE_DIR,exist_ok=True)
app_page='home'   # home / editor / community
profile_name=''
profile_slug=''
project_name=''
project_file=None
home_input_mode='profile'  # profile / project / rename
home_input=''
login_username=''  # persistent text for creator name field
home_notice='Create a local creator profile to get started.'
home_scroll=0
home_confirm_delete=None


def safe_slug(name):
    slug=re.sub(r'[^a-zA-Z0-9_-]+','_',name.strip()).strip('_')
    return slug[:48] or 'creator'


def profile_path(slug):
    return os.path.join(PROFILE_DIR,slug)


def profiles_available():
    return sorted(d for d in os.listdir(PROFILE_DIR)
                  if os.path.isdir(profile_path(d)))


def project_folder():
    return os.path.join(profile_path(profile_slug),'projects')


def project_list():
    if not profile_slug:return []
    folder=project_folder()
    os.makedirs(folder,exist_ok=True)
    return sorted((f for f in os.listdir(folder) if f.endswith('.json')),
                  key=lambda f:os.path.getmtime(os.path.join(folder,f)),reverse=True)


def unique_project_path(name):
    base=safe_slug(name)
    folder=project_folder()
    os.makedirs(folder,exist_ok=True)
    path=os.path.join(folder,base+'.json')
    n=2
    while os.path.exists(path):
        path=os.path.join(folder,f'{base}_{n}.json');n+=1
    return path


# Passwords are stored as salted PBKDF2 hashes, never plaintext.
# This is local profile protection, not a network account service.
login_password=''
password_focus=False

def password_hash(password,salt):
    return hashlib.pbkdf2_hmac('sha256',password.encode('utf-8'),bytes.fromhex(salt),350000).hex()

def choose_profile(name):
    global profile_name,profile_slug,app_page,home_notice,home_input,login_password,home_scroll,login_username,password_focus
    if not name.strip():
        home_notice='Enter your creator name.';return
    slug=safe_slug(name)
    folder=profile_path(slug)
    meta=os.path.join(folder,'profile.json')
    if os.path.exists(meta):
        try:
            with open(meta,encoding='utf-8') as f:record=json.load(f)
        except (OSError,ValueError) as e:
            home_notice='Profile could not be read: '+str(e);return
        if 'password_hash' in record:
            if not login_password:
                home_notice='Enter your password to sign in.';return
            actual=password_hash(login_password,record['salt'])
            if not hmac.compare_digest(actual,record['password_hash']):
                home_notice='Incorrect password. Try again.';login_password='';return
        else:
            # Legacy v8 profile: require the creator to set a password on first upgrade.
            if len(login_password)<8:
                home_notice='Old profile found. Set a password (8+ characters) to upgrade.';return
            salt=secrets.token_hex(16)
            record={'name':record.get('name',name.strip()),'salt':salt,
                    'password_hash':password_hash(login_password,salt)}
            with open(meta,'w',encoding='utf-8') as f:json.dump(record,f,indent=2)
    else:
        if len(login_password)<8:
            home_notice='New profiles need a password of at least 8 characters.';return
        os.makedirs(os.path.join(folder,'projects'),exist_ok=True)
        salt=secrets.token_hex(16)
        record={'name':name.strip(),'salt':salt,'password_hash':password_hash(login_password,salt)}
        with open(meta,'w',encoding='utf-8') as f:json.dump(record,f,indent=2)
    os.makedirs(os.path.join(folder,'projects'),exist_ok=True)
    profile_slug=slug;profile_name=record.get('name',name.strip())
    home_input='';login_username=profile_name;login_password='';password_focus=False;home_scroll=0
    app_page='home';home_notice='Welcome back, '+profile_name+'!'

def log_out():
    global profile_name,profile_slug,project_name,project_file,home_input,login_password
    global home_notice,home_input_mode,app_page,password_focus,login_username
    if app_page=='editor' and not save_current_project():return
    if ONLINE_MODE:online_sign_out()
    profile_name='';profile_slug='';project_name='';project_file=None
    home_input='';login_username='';login_password='';home_input_mode='profile';password_focus=False
    home_notice='Signed out. Enter creator name and password.';app_page='home'


def scene_default_dialogue_style():
    return {'bg':'#191C2A','border':'#F5A3C9','text':'#FFFFFF','opacity':235,'height':90,'image':None}

def parse_hex_color(value):
    value=str(value).strip().lstrip('#')
    if len(value)!=6 or any(c not in '0123456789abcdefABCDEF' for c in value):
        raise ValueError('Use six-digit colors like #FF99CC')
    return tuple(int(value[n:n+2],16) for n in (0,2,4))

# Sound paths are saved per scene; actual Sound objects are cached only in memory.
scene_sounds = {}
_sound_cache = {}
_sound_channels = {}
_current_music_name = None

def play_sound(name, loop=False):
    global message,_current_music_name
    path = scene_sounds.get(name, name)
    if not os.path.isfile(path):
        message = 'Sound not found: ' + name
        return
    try:
        if not pygame.mixer.get_init():
            pygame.mixer.init()
        if path.lower().endswith('.mp3'):
            pygame.mixer.music.load(path)
            pygame.mixer.music.play(-1 if loop else 0)
            _current_music_name=name
        else:
            if path not in _sound_cache:
                _sound_cache[path] = pygame.mixer.Sound(path)
            channel=_sound_cache[path].play(loops=-1 if loop else 0)
            if channel is not None:_sound_channels[name]=channel
        message = 'Playing sound: ' + name
    except (pygame.error, OSError) as exc:
        message = 'Audio error: ' + str(exc)[:95]

def stop_sound(name=None):
    """Stop one named track, or all tracks when no name is given."""
    global _current_music_name
    if not pygame.mixer.get_init():return
    if not name:
        pygame.mixer.stop();pygame.mixer.music.stop()
        _sound_channels.clear();_current_music_name=None
        return
    if _current_music_name==name:
        pygame.mixer.music.stop();_current_music_name=None
    channel=_sound_channels.pop(name,None)
    if channel is not None:channel.stop()

def item_by_name(name):
    return next((i for i in items if i['name'].lower() == name.lower()), None)

def item_world_position(item, visited=None):
    visited = set() if visited is None else visited
    if item['id'] in visited:
        return item['x'], item['y']
    visited.add(item['id'])
    parent = next((i for i in items if i['id'] == item.get('parent_id')), None)
    if parent is None:
        return item['x'], item['y']
    px, py = item_world_position(parent, visited)
    return px + item['x'], py + item['y']

def attach_item(child, parent):
    if child['id'] == parent['id']:
        raise ValueError('An item cannot parent itself')
    ancestor = parent
    visited = set()
    while ancestor and ancestor['id'] not in visited:
        if ancestor['id'] == child['id']:
            raise ValueError('Parenting would create a cycle')
        visited.add(ancestor['id'])
        ancestor = next((i for i in items if i['id'] == ancestor.get('parent_id')), None)
    cx, cy = item_world_position(child)
    px, py = item_world_position(parent)
    child['parent_id'] = parent['id']
    child['x'], child['y'] = cx-px, cy-py

def detach_item(child):
    x, y = item_world_position(child)
    child.pop('parent_id', None)
    child['x'], child['y'] = x, y

def selected_animation(item):
    name=item.get('active_animation')
    return (item.get('animations') or {}).get(name) if name else None

def play_named_animation(item,name,operation='PLAY'):
    clips=item.get('animations',{})
    if name not in clips:raise ValueError('Animation not found: '+name)
    if not clips[name].get('frames'):raise ValueError('Add PNG frames to '+name+' first')
    if operation=='TOGGLE' and item.get('_anim_playing') and item.get('active_animation')==name:
        item['_anim_playing']=False
    else:
        item['active_animation']=name
        item['_anim_playing']=True
        item['_anim_started']=pygame.time.get_ticks()
        item.pop('_current_frame_path',None)

def reset_project():
    global items,selected_id,next_id,background,bg_path,bg_rect,scenes,scene_order
    global current_scene,scene_type,movement_enabled,player_id,scene_choices
    global dialogue,scene_dialogue,dialogue_lines,dialogue_index,collision_enabled,scene_events
    global story_flags,video_path,mode,active_panel,choice_open,dialogue_style,scene_sounds
    global entity_folders,entity_assignments,audio_assignments,entity_folder_closed,entity_scroll
    items=[];selected_id=None;next_id=1; background=None;bg_path=None
    bg_rect=[0,0,*canvas_rect().size]
    scenes={'Scene 1':{'background':None,'bg_rect':list(bg_rect),
        'items':[],'type':'exploration','movement':True,'player_id':None,
        'dialogue':'','choices':[],'dialogue_lines':[],'collision':True,'events':{},'dialogue_style':copy.deepcopy(scene_default_dialogue_style())}}
    scene_order=['Scene 1'];current_scene='Scene 1';scene_type='exploration'
    movement_enabled=True;player_id=None;scene_choices=[];dialogue=''
    scene_dialogue='';scene_events={};dialogue_style=scene_default_dialogue_style();scene_sounds={};dialogue_lines=[];dialogue_index=0
    collision_enabled=True;story_flags={};video_path=None
    entity_folders=[];entity_assignments={};audio_assignments={};entity_folder_closed=set();entity_scroll=0
    mode='edit';active_panel='Scenes';choice_open=False


def save_current_project():
    global message,home_notice
    if not project_file:return False
    store_scene()
    data={'format_version':8,'project_name':project_name,'scenes':scenes,
          'scene_order':scene_order,'current_scene':current_scene,
          'flags':story_flags,'video':video_path}
    try:
        with open(project_file,'w',encoding='utf-8') as f:
            json.dump(data,f,indent=2)
        message='Saved '+project_name
        home_notice='Saved '+project_name
        return True
    except (OSError,TypeError,ValueError) as e:
        message='Save failed: '+str(e);home_notice=message
        return False


def open_project_file(path):
    global scenes,scene_order,current_scene,story_flags,video_path
    global project_name,project_file,app_page,message,home_notice
    try:
        with open(path,encoding='utf-8') as f:data=json.load(f)
        if not isinstance(data.get('scenes'),dict) or not data['scenes']:
            raise ValueError('Invalid project format')
        new_scenes=data['scenes']
        new_order=[n for n in data.get('scene_order',list(new_scenes)) if n in new_scenes]
        if not new_order:raise ValueError('No scenes found')
        scenes=new_scenes;scene_order=new_order
        story_flags=data.get('flags',{});video_path=data.get('video')
        target=data.get('current_scene',new_order[0])
        if target not in scenes:target=new_order[0]
        # Reset editor state before loading; never overwrite loaded scenes with stale editor data.
        current_scene=target
        reset_project()
        scenes=new_scenes;scene_order=new_order;current_scene=target
        story_flags=data.get('flags',{});video_path=data.get('video')
        change_scene(target)
        project_file=path
        project_name=data.get('project_name',Path(path).stem)
        app_page='editor';message='Opened '+project_name
    except (OSError,ValueError,TypeError,KeyError) as e:
        home_notice='Could not open project: '+str(e)


def create_project(name):
    global project_name,project_file,app_page,home_notice
    if not name.strip():home_notice='Enter a project name.';return
    project_name=name.strip()
    project_file=unique_project_path(project_name)
    reset_project()
    if save_current_project():app_page='editor'


def go_home():
    global app_page,home_confirm_delete,home_notice
    if save_current_project():
        app_page='home';home_confirm_delete=None
        home_notice='Project saved. Select a project below.'


def gallery_layout():
    ww,hh=screen.get_size();cx=ww//2
    width=min(920,max(520,ww-70))
    left=cx-width//2
    top=155
    cols=2 if width>=680 else 1
    gap=16
    card_w=(width-52-(cols-1)*gap)//cols
    card_h=112
    start_y=405
    visible_rows=max(1,(hh-95-start_y+12)//(card_h+gap))
    return left,width,cols,gap,card_w,card_h,start_y,visible_rows


def home_buttons():
    ww,hh=screen.get_size();cx=ww//2
    left,width,*_=gallery_layout()
    return {'profile_entry':pygame.Rect(cx-260,195,360,42),
            'profile_submit':pygame.Rect(cx+110,251,150,42),
            'password_entry':pygame.Rect(cx-260,251,360,42),
            'logout':pygame.Rect(left+width-122,159,110,33),
            'project_entry':pygame.Rect(left+18,258,width-195,44),
            'project_submit':pygame.Rect(left+width-160,258,142,44),
            'scroll_up':pygame.Rect(left+width-118,320,50,30),
            'scroll_down':pygame.Rect(left+width-60,320,50,30),
            'discover':pygame.Rect(left+211,314,min(216,width-350),40),
            'projects_tab':pygame.Rect(left+18,314,185,40),
            'settings':pygame.Rect(left+width-124,314,106,40)}


def gallery_card_rect(index):
    left,width,cols,gap,card_w,card_h,start_y,visible_rows=gallery_layout()
    return pygame.Rect(left+18+(index%cols)*(card_w+gap),
                       start_y+(index//cols)*(card_h+gap),card_w,card_h)


def publish_modal_draw():
    if not publish_modal['open']: return
    w,h=screen.get_size()
    veil=pygame.Surface((w,h),pygame.SRCALPHA);veil.fill((0,0,0,175));screen.blit(veil,(0,0))
    r=pygame.Rect(w//2-350,h//2-240,700,480)
    pygame.draw.rect(screen,COL['panel'],r,border_radius=15)
    pygame.draw.rect(screen,COL['accent'],r,3,border_radius=15)
    draw_text(ui('Publish your game'),r.x+24,r.y+22,COL['accent'],BIG)
    draw_text(ui('Title'),r.x+24,r.y+90,COL['muted'],SMALL)
    draw_text(ui('Description'),r.x+24,r.y+168,COL['muted'],SMALL)
    draw_text(ui('Cover image (optional)'),r.x+24,r.y+292,COL['muted'],SMALL)
    for field,y,height in [('title',112,43),('description',190,87)]:
        box=pygame.Rect(r.x+24,r.y+y,r.w-48,height)
        pygame.draw.rect(screen,COL['bg'],box,border_radius=6)
        pygame.draw.rect(screen,COL['accent'] if publish_modal['field']==field else COL['line'],box,2,border_radius=6)
        text=publish_modal[field].replace('\n',' ')
        maxchars=55 if field!='description' else 115
        draw_text(text[-maxchars:] + ('|' if publish_modal['field']==field and pygame.time.get_ticks()//500%2==0 else ''),box.x+10,box.y+9,COL['text'],SMALL)
    cover_box=pygame.Rect(r.x+24,r.y+314,r.w-48,43)
    button(cover_box,ui('Choose Image...') if not publish_modal['cover'] else ui('Change Image...'),small=True)
    if publish_modal['cover']:
        draw_text(Path(publish_modal['cover']).name[:36],cover_box.x+180,cover_box.y+10,COL['text'],SMALL)
    draw_text(ui('Tab: next field  •  Enter: publish  •  Esc: cancel'),r.x+24,r.y+370,COL['muted'],SMALL)
    button(pygame.Rect(r.x+24,r.bottom-66,130,40),ui('Cancel'),small=True)
    button(pygame.Rect(r.right-190,r.bottom-66,166,40),ui('Publish'),small=True)

def publish_modal_commit():
    global home_notice
    path=publish_modal['path']
    if not os.path.isfile(path):home_notice='Project no longer exists.';publish_modal['open']=False;return
    title=publish_modal['title'].strip() or Path(path).stem
    description=publish_modal['description'].strip()
    cover=publish_modal['cover'].strip()
    open_project_file(path)
    if app_page!='editor':publish_modal['open']=False;return
    archive=community_publish_local(project_file,profile_name,title,export_game,description,cover)
    if archive and publish_modal['online']:
        if ONLINE_MODE:online_publish_game(archive,title)
        else:community_state['notice']='Online Account required for public publishing.'
    home_notice=community_state['notice']
    globals()['app_page']='home'
    publish_modal['open']=False

def publish_modal_click(pos):
    w,h=screen.get_size();r=pygame.Rect(w//2-350,h//2-240,700,480)
    if pygame.Rect(r.x+24,r.bottom-66,130,40).collidepoint(pos):publish_modal['open']=False;return
    if pygame.Rect(r.right-190,r.bottom-66,166,40).collidepoint(pos):publish_modal_commit();return
    if pygame.Rect(r.x+24,r.y+314,r.w-48,43).collidepoint(pos):
        picked=choose_file('Choose a cover image (PNG or JPEG)',kind='image')
        if picked and Path(picked).suffix.lower() in ('.png','.jpg','.jpeg'):
            publish_modal['cover']=picked
        return
    for field,y,height in [('title',112,43),('description',190,87)]:
        if pygame.Rect(r.x+24,r.y+y,r.w-48,height).collidepoint(pos):publish_modal['field']=field;return

def publish_modal_key(event):
    if event.key==pygame.K_ESCAPE:publish_modal['open']=False;return
    if event.key==pygame.K_TAB:
        fields=['title','description'];publish_modal['field']=fields[(fields.index(publish_modal['field'])+1)%2];return
    if event.key==pygame.K_RETURN:publish_modal_commit();return
    f=publish_modal['field']
    if f not in ('title','description'):return
    if event.key==pygame.K_BACKSPACE:publish_modal[f]=publish_modal[f][:-1]
    elif event.unicode and event.unicode.isprintable() and len(publish_modal[f]) < {'title':80,'description':1200}[f]:publish_modal[f]+=event.unicode

def draw_home():
    screen.fill(COL['bg'])
    ww,hh=screen.get_size();cx=ww//2
    title='2d appmaker';surf=BIG.render(title,True,COL['accent'])
    tx=cx-surf.get_width()//2-12
    screen.blit(surf,(tx,35))
    hx=tx+surf.get_width()+17;hy=47
    pygame.draw.circle(screen,COL['accent'],(hx-6,hy-3),7)
    pygame.draw.circle(screen,COL['accent'],(hx+6,hy-3),7)
    pygame.draw.polygon(screen,COL['accent'],[(hx-13,hy),(hx+13,hy),(hx,hy+16)])
    tagline=editor_ui('Your cozy little game studio')
    sub=interface_font(FONT,tagline).render(display_safe(tagline),True,COL['muted'])
    screen.blit(sub,(cx-sub.get_width()//2,82))
    r=home_buttons()
    if not profile_slug:
        panel=pygame.Rect(cx-300,132,600,max(380,hh-164))
        pygame.draw.rect(screen,COL['panel'],panel,border_radius=12)
        pygame.draw.rect(screen,COL['line'],panel,2,border_radius=12)
        draw_text('CREATOR PROFILE',cx-260,142,COL['accent'])
        draw_text('Username',r['profile_entry'].x,r['profile_entry'].y-23,COL['muted'],SMALL)
        entry=r['profile_entry']
        pygame.draw.rect(screen,COL['bg'],entry,border_radius=5)
        pygame.draw.rect(screen,COL['accent'] if not password_focus else COL['line'],entry,2,border_radius=5)
        draw_text((login_username or 'Creator name')[-34:]+('|' if home_input_mode=='profile' and not password_focus and pygame.time.get_ticks()//500%2==0 else ''),entry.x+9,entry.y+10,COL['text'] if login_username else COL['muted'])
        pw=r['password_entry']
        pygame.draw.rect(screen,COL['bg'],pw,border_radius=5)
        pygame.draw.rect(screen,COL['accent'] if password_focus else COL['line'],pw,2,border_radius=5)
        draw_text((('*'*min(len(login_password),32)) if login_password else 'Password (8+ characters for new accounts)')+('|' if password_focus and pygame.time.get_ticks()//500%2==0 else ''),pw.x+9,pw.y+10,COL['text'] if login_password else COL['muted'],SMALL)
        button(r['profile_submit'],'Sign In / Create',small=True)
        draw_text('Sign in or create a profile to see your projects.',cx-260,335,COL['muted'])
        button(pygame.Rect(cx-260,377,520,44),'Online Account - Sign In / Create',small=True)
        draw_text('Local profiles stay available; online accounts are separate.',cx-260,437,COL['muted'],SMALL)
    else:
        left,width,cols,gap,card_w,card_h,start_y,visible_rows=gallery_layout()
        panel=pygame.Rect(left,132,width,max(420,hh-156))
        pygame.draw.rect(screen,COL['panel'],panel,border_radius=12)
        pygame.draw.rect(screen,COL['line'],panel,2,border_radius=12)
        draw_text('Welcome, '+profile_name[:28]+'!',left+18,158,COL['accent'])
        button(r['logout'],'Log Out',small=True)
        draw_text('ONLINE' if ONLINE_MODE else 'LOCAL',left+width-230,168,COL['accent'],SMALL)
        draw_text(ui('NEW PROJECT'),left+18,222,COL['accent'])
        entry=r['project_entry']
        pygame.draw.rect(screen,COL['bg'],entry,border_radius=6)
        pygame.draw.rect(screen,COL['accent'] if home_input_mode in ('project','rename') else COL['line'],entry,2,border_radius=6)
        value=home_input if home_input_mode in ('project','rename') else ''
        draw_text((value or ui('Name your new game...'))[-38:]+('|' if home_input_mode=='project' and pygame.time.get_ticks()//500%2==0 else ''),entry.x+12,entry.y+11,COL['text'] if value else COL['muted'])
        button(r['project_submit'],'+ Create',small=True)
        # Matching navigation tabs: current page is pink, community tab is outlined.
        pygame.draw.rect(screen,COL['accent'],r['projects_tab'],border_radius=9)
        pygame.draw.rect(screen,COL['accent'],r['discover'],2,border_radius=9)
        tab_font=SMALL
        for label,rect,color in [('My Projects',r['projects_tab'],(49,38,39) if settings_prefs['accent']=='Peach' else COL['bg']),
                                 ("Play Others' Games",r['discover'],COL['text'])]:
            label_surf=interface_font(tab_font,ui(label)).render(display_safe(ui(label)),True,color)
            screen.blit(label_surf,label_surf.get_rect(center=rect.center))
        button(r['settings'],'Settings',small=True)
        draw_text(ui('YOUR PROJECTS'),left+18,375,COL['accent'],SMALL)
        all_projects=project_list()
        max_page=max(0,(len(all_projects)-1)//cols-visible_rows+1)
        scroll=min(home_scroll,max_page)
        if scroll>0:button(r['scroll_up'],'Up',small=True)
        if scroll<max_page:button(r['scroll_down'],'Down',small=True)
        old=screen.get_clip()
        screen.set_clip(pygame.Rect(left+10,start_y-2,width-20,max(1,hh-start_y-80)))
        for idx,f in enumerate(all_projects[scroll*cols:(scroll+visible_rows)*cols]):
            card=gallery_card_rect(idx)
            pygame.draw.rect(screen,COL['selected'],card,border_radius=11)
            pygame.draw.rect(screen,COL['line'],card,1,border_radius=11)
            draw_text('PROJECT',card.x+14,card.y+10,COL['accent'],SMALL)
            draw_text(Path(f).stem[:max(12,(card.w-30)//11)],card.x+14,card.y+34,COL['text'])
            draw_text(ui('Click card to open'),card.x+14,card.y+60,COL['muted'],SMALL)
            button(pygame.Rect(card.x+12,card.bottom-31,88,26),'Publish',small=True)
            button(pygame.Rect(card.x+106,card.bottom-31,77,26),'Online',small=True)
            button(pygame.Rect(card.right-153,card.bottom-31,69,26),'Rename',small=True)
            button(pygame.Rect(card.right-79,card.bottom-31,72,26),'Delete',small=True)
        screen.set_clip(old)
        if not all_projects:draw_text('No projects yet. Create your first game above!',left+18,start_y+20,COL['muted'])
    draw_text(home_notice[:78],cx-260,hh-51,COL['accent'],SMALL)
    if home_confirm_delete:draw_text('Click Delete again to confirm.',cx-260,hh-75,COL['red'],SMALL)
    if settings_open:draw_settings()
    if publish_modal['open']:publish_modal_draw()
    pygame.display.flip()


def home_click(pos):
    if publish_modal['open']:publish_modal_click(pos);return
    global settings_open
    if settings_open:
        settings_click(pos);return
    global home_input_mode,home_input,home_notice,home_scroll,home_confirm_delete,password_focus
    r=home_buttons()
    if not profile_slug:
        if pygame.Rect(screen.get_width()//2-260,377,520,44).collidepoint(pos):
            globals()['ONLINE_AUTH_SCREEN']=True;return
        if r['profile_entry'].collidepoint(pos):home_input_mode='profile';password_focus=False;return
        if r['password_entry'].collidepoint(pos):home_input_mode='profile';password_focus=True;return
        if r['profile_submit'].collidepoint(pos):choose_profile(login_username);return
        return
    if r['settings'].collidepoint(pos):settings_open=True;return
    if r['logout'].collidepoint(pos):log_out();return
    if r['discover'].collidepoint(pos):
        globals()['app_page']='community'
        community_state['scope']='local'
        if ONLINE_SETTINGS.get('url'):online_gallery_refresh()
        return
    if r['project_entry'].collidepoint(pos):home_input_mode='project';password_focus=False;return
    if r['project_submit'].collidepoint(pos):create_project(home_input);home_input='';return
    projects=project_list()
    left,width,cols,gap,card_w,card_h,start_y,visible_rows=gallery_layout()
    max_page=max(0,(len(projects)-1)//cols-visible_rows+1)
    if r['scroll_up'].collidepoint(pos):home_scroll=max(0,home_scroll-1);return
    if r['scroll_down'].collidepoint(pos):home_scroll=min(max_page,home_scroll+1);return
    for idx,f in enumerate(projects[home_scroll*cols:(home_scroll+visible_rows)*cols]):
        card=gallery_card_rect(idx)
        if not card.collidepoint(pos) or pos[1]>screen.get_height()-80:continue
        path=os.path.join(project_folder(),f)
        publish=pygame.Rect(card.x+12,card.bottom-31,88,26)
        online=pygame.Rect(card.x+106,card.bottom-31,77,26)
        if publish.collidepoint(pos) or online.collidepoint(pos):
            publish_modal.update(open=True,path=path,online=online.collidepoint(pos),title=Path(f).stem,description='',cover='',field='title')
            return
        rename=pygame.Rect(card.right-153,card.bottom-31,69,26)
        delete=pygame.Rect(card.right-79,card.bottom-31,72,26)
        if delete.collidepoint(pos):
            if home_confirm_delete==path or not settings_prefs.get('confirm_delete',True):
                try:
                    os.remove(path)
                    count=community_remove_local_project(path,profile_name,Path(f).stem)
                    home_notice='Project and '+str(count)+' local publication(s) deleted. Online copies must be removed separately.'
                except OSError as e:home_notice=str(e)
                home_confirm_delete=None
            else:home_confirm_delete=path;home_notice='Click Delete again to confirm.'
            return
        home_confirm_delete=None
        if rename.collidepoint(pos):
            home_input_mode='rename';home_input=Path(f).stem
            home_notice='Edit name above, then press Enter.'
            globals()['rename_target']=path
            return
        open_project_file(path);return


def home_key(event):
    if publish_modal['open']:publish_modal_key(event);return
    global settings_open
    if settings_open:
        if event.key==pygame.K_ESCAPE:settings_open=False
        return
    global home_input,home_input_mode,home_notice,login_password,password_focus,login_username
    if event.key==pygame.K_TAB:
        if home_input_mode in ('project','rename'):
            home_input_mode='profile';password_focus=False
        else:
            home_input_mode='profile';password_focus=not password_focus
        return
    if password_focus:
        if event.key==pygame.K_BACKSPACE:login_password=login_password[:-1]
        elif event.key==pygame.K_ESCAPE:password_focus=False
        elif event.key==pygame.K_RETURN:choose_profile(login_username)
        elif event.unicode and event.unicode.isprintable() and len(login_password)<128:
            login_password+=event.unicode
        return
    if event.key==pygame.K_BACKSPACE:
        if home_input_mode=='profile':login_username=login_username[:-1]
        else:home_input=home_input[:-1]
    elif event.key==pygame.K_ESCAPE:
        if home_input_mode=='profile':login_username=''
        else:home_input=''
    elif event.key==pygame.K_RETURN:
        if home_input_mode=='profile':
            password_focus=True
            home_notice='Now enter your password, then click Sign In / Create.'
        elif home_input_mode=='project':create_project(home_input)
        elif home_input_mode=='rename':
            old=globals().get('rename_target')
            if old and os.path.isfile(old) and home_input.strip():
                new=unique_project_path(home_input)
                try:
                    with open(old,encoding='utf-8') as f:data=json.load(f)
                    data['project_name']=home_input.strip()
                    with open(new,'w',encoding='utf-8') as f:json.dump(data,f,indent=2)
                    os.remove(old)
                    home_notice='Project renamed.'
                except (OSError,ValueError) as e:home_notice='Rename failed: '+str(e)
            home_input_mode='project'
        home_input=''
    elif event.unicode and event.unicode.isprintable():
        if home_input_mode=='profile' and len(login_username)<48:login_username+=event.unicode
        elif home_input_mode!='profile' and len(home_input)<48:home_input+=event.unicode



def serialize_item(item):
    return {k:copy.deepcopy(v) for k,v in item.items()
            if k not in ('image','_frame_image','_current_frame_path') and not k.startswith('_')}


def hydrate_item(data):
    item=copy.deepcopy(data)
    item['image']=None
    path=item.get('path')
    if path and os.path.isfile(path):
        try:item['image']=pygame.image.load(path).convert_alpha()
        except (OSError,pygame.error):pass
    return item


def capture_scene():
    return {'background':bg_path,'bg_rect':list(bg_rect),
            'items':[serialize_item(i) for i in items],
            'map_width':map_width,'map_height':map_height,
            'type':scene_type,'movement':movement_enabled,
            'player_id':player_id,'dialogue':scene_dialogue,
            'choices':copy.deepcopy(scene_choices),'dialogue_lines':copy.deepcopy(dialogue_lines),'collision':collision_enabled,'events':copy.deepcopy(scene_events),'dialogue_style':copy.deepcopy(dialogue_style),'sounds':copy.deepcopy(scene_sounds),'entity_folders':list(entity_folders),'entity_assignments':dict(entity_assignments),'audio_assignments':dict(audio_assignments)}


def store_scene():
    scenes[current_scene]=capture_scene()


def change_scene(name):
    global current_scene,items,background,bg_path,bg_rect,selected_id
    global scene_type,movement_enabled,player_id,scene_choices,choice_open
    global dialogue,scene_dialogue,next_id,message,mode,dialogue_lines,dialogue_index,collision_enabled,scene_events,dialogue_style,scene_sounds
    global entity_folders,entity_assignments,audio_assignments,entity_folder_closed,entity_scroll,selected_drawer_sound
    global map_width,map_height,editor_pan,play_camera
    if name not in scenes:
        message='Scene not found: '+name
        return False
    if name!=current_scene:
        if mode=='play':
            editor_event({'events':scene_events},'end')
        store_scene()
    data=scenes[name]
    map_width=max(200,int(data.get('map_width',3000)))
    map_height=max(200,int(data.get('map_height',2000)))
    editor_pan=[0.0,0.0];play_camera=[0.0,0.0]
    current_scene=name
    bg_path=data.get('background')
    bg_rect=list(data.get('bg_rect',[0,0,*canvas_rect().size]))
    background=None
    if bg_path and os.path.isfile(bg_path):
        try:background=pygame.image.load(bg_path).convert_alpha()
        except (OSError,pygame.error):pass
    items[:]=[hydrate_item(i) for i in data.get('items',[])]
    selected_id=None
    next_id=max([i['id'] for i in items],default=0)+1
    scene_type=data.get('type','exploration')
    movement_enabled=bool(data.get('movement',scene_type=='exploration'))
    player_id=data.get('player_id')
    scene_dialogue=data.get('dialogue','')
    scene_events=copy.deepcopy(data.get('events',{}))
    dialogue_style={**scene_default_dialogue_style(),**data.get('dialogue_style',{})}
    scene_sounds=copy.deepcopy(data.get('sounds',{}))
    entity_folders=list(data.get('entity_folders',[]))
    entity_assignments=dict(data.get('entity_assignments',{}))
    audio_assignments=dict(data.get('audio_assignments',{}))
    entity_folder_closed=set();entity_scroll=0;selected_drawer_sound=None
    scene_choices=copy.deepcopy(data.get('choices',[]))
    dialogue_lines=copy.deepcopy(data.get('dialogue_lines',[]))
    dialogue_index=0
    collision_enabled=data.get('collision',True)
    dialogue=(dialogue_lines[0] if dialogue_lines else scene_dialogue)
    choice_open=False
    message='Scene: '+name+' ('+scene_type+')'
    if mode=='play':editor_start_events()
    return True


def add_scene(name):
    global current_scene,scene_type,movement_enabled,player_id,scene_choices
    global scene_dialogue,dialogue,background,bg_path,bg_rect,selected_id,next_id,message
    name=name.strip()
    if not name or name in scenes:
        message='Scene name must be unique and nonempty.'
        return
    store_scene()
    scenes[name]={'background':None,'bg_rect':[0,0,3000,2000],'map_width':3000,'map_height':2000,
                  'items':[],'type':'exploration','movement':True,
                  'player_id':None,'dialogue':'','choices':[],'dialogue_lines':[],'collision':True}
    scene_order.append(name)
    current_scene=name
    change_scene(name)


def scene_cycle_type():
    global scene_type,movement_enabled,message
    types=['exploration','dialogue','choice']
    scene_type=types[(types.index(scene_type)+1)%3]
    movement_enabled=(scene_type=='exploration')
    message='Scene type: '+scene_type


def set_scene_dialogue(text):
    global scene_dialogue,dialogue,dialogue_started_ms
    scene_dialogue=str(text)
    dialogue=scene_dialogue
    dialogue_started_ms=pygame.time.get_ticks()


def activate_choice(index):
    global choice_open,message
    if 0<=index<len(scene_choices):
        entry=scene_choices[index]
        target=entry.get('target','')
        if target in scenes:
            choice_open=False
            previous=current_scene
            editor_event({'events':scene_events},'choice:'+entry.get('label','').lower())
            if current_scene==previous:change_scene(target)
        else:
            message='Choice destination missing: '+target


def fire_dialogue_end():
    """Dispatch when the player advances a fully revealed dialogue line."""
    if mode!='play':return
    origin=current_scene
    editor_event({'events':scene_events},'dialogue_end')
    if current_scene!=origin:return
    for obj in list(items):
        editor_event(obj,'dialogue_end')
        if current_scene!=origin:break

def advance_dialogue():
    global dialogue_index,dialogue,message,dialogue_started_ms
    if _npc_dialogue_queue and not dialogue_lines:
        for (sc,oid) in list(_npc_dialogue_queue):
            if sc==current_scene:
                obj=next((i for i in items if i.get('id')==oid),None)
                if obj:editor_event(obj,'click');return
        return
    if dialogue and typewriter_cps>0:
        shown=int((pygame.time.get_ticks()-dialogue_started_ms)*typewriter_cps/1000)
        if shown<len(dialogue):
            dialogue_started_ms=pygame.time.get_ticks()-int(len(dialogue)*1000/typewriter_cps)
            return
    if not dialogue:
        message='No active dialogue.'
        return
    fire_dialogue_end()
    if not dialogue_lines:
        dialogue=''
        message='Dialogue finished.'
        return
    dialogue_index+=1
    if dialogue_index>=len(dialogue_lines):
        dialogue_index=0
        dialogue=''
        message='Dialogue finished. Press Space to replay.'
    else:
        dialogue=dialogue_lines[dialogue_index]
        dialogue_started_ms=pygame.time.get_ticks()
        message='Dialogue '+str(dialogue_index+1)+'/'+str(len(dialogue_lines))


def movement_allowed(item, new_x, new_y):
    canvas=canvas_rect()
    proposed=pygame.Rect(int(new_x),int(new_y),item['w'],item['h'])
    if proposed.left<0 or proposed.top<0 or proposed.right>map_width or proposed.bottom>map_height:
        return False
    if collision_enabled:
        for other in items:
            if other is item or not other.get('visible',True):continue
            # Objects are solid obstacles. Characters also block each other.
            if other['kind'] not in ('object','character'):continue
            if proposed.colliderect(pygame.Rect(other['x'],other['y'],other['w'],other['h'])):
                return False
    return True


def story_flow_nodes(side):
    # Scrollable vertical node graph of scenes; links are drawn to visible targets.
    area=pygame.Rect(side.x+12,side.y+438,side.w-24,max(70,side.h-472))
    nodes={}
    for j,name in enumerate(scene_order):
        nodes[name]=pygame.Rect(area.x+14,area.y+12+j*85-branch_scroll*85,area.w-28,55)
    return area,nodes


def draw_story_flow(side):
    draw_text('STORY FLOW  |  click a scene to edit',side.x+14,side.y+417,COL['accent'],SMALL)
    area,nodes=story_flow_nodes(side)
    old=screen.get_clip();screen.set_clip(area)
    for source in scene_order:
        data=capture_scene() if source==current_scene else scenes[source]
        for choice in data.get('choices',[]):
            target=choice.get('target')
            if target in nodes:
                a=nodes[source];b=nodes[target]
                pygame.draw.line(screen,COL['accent'],(a.centerx,a.bottom),(b.centerx,b.top),2)
    for name,rect in nodes.items():
        if rect.bottom<area.top or rect.top>area.bottom:continue
        button(rect,name[:24],selected=name==current_scene,small=True)
        data=capture_scene() if name==current_scene else scenes[name]
        draw_text(data.get('type','exploration')[:16],rect.x+9,rect.y+35,COL['muted'],SMALL)
    screen.set_clip(old)
    draw_text('Add links: CHOICE Yes -> Scene 2',side.x+14,side.bottom-21,COL['muted'],SMALL)

def scene_ui_rects(side):
    x=side.x+12;y=side.y+457;w=side.w-24
    return {
      'new':pygame.Rect(x,y,w//2-4,26),
      'next':pygame.Rect(x+w//2+4,y,w//2-4,26),
      'type':pygame.Rect(x,y+26,w,25),
      'movement':pygame.Rect(x,y+52,w,25),
      'player':pygame.Rect(x,y+78,w,25),
      'dialogue':pygame.Rect(x,y+104,w,25),
      'choice':pygame.Rect(x,y+130,w,25),
      'collision':pygame.Rect(x,y+156,w,25),
      'timeline':pygame.Rect(x,y+182,w,25),
    }

# Initialize the first scene before the event loop.
scenes['Scene 1']={'background':None,'bg_rect':[0,0,800,600],
                   'items':[],'type':'exploration','movement':True,
                   'player_id':None,'dialogue':'','choices':[],'dialogue_lines':[],'collision':True}

# Files are selected with macOS AppleScript, avoiding tkinter's macOS crash.
def choose_file(prompt, kind='png', save=False):
    if sys.platform == 'emscripten':
        # Browser native file picker cannot synchronously return a filesystem path.
        # We explicitly disable this until a proper async upload bridge is installed.
        global message
        message = 'Browser file picker is not yet supported. Use the desktop version for asset import.'
        return None
    if save:
        script='set p to choose file name with prompt '+json.dumps(prompt)+'\nreturn POSIX path of p'
    else:
        type_clause=' of type {"public.png"}' if kind=='png' else ''
        script='set p to choose file with prompt '+json.dumps(prompt)+type_clause+'\nreturn POSIX path of p'
    try:
        p=subprocess.run(['osascript','-e',script],capture_output=True,text=True)
        return p.stdout.strip() if p.returncode==0 else None
    except OSError:
        return None

def display_safe(s):
    # Some pygame/macOS font combinations render these as empty squares.
    return str(s).replace('♡', '').replace('▶', '>').replace('←', '<').replace('▲', '^').replace('▼', 'v')

# v33: interface language preferences. Game dialogue remains author-controlled.
UI_TRANSLATIONS={
 'English':{},
 'Français':{'My Projects':'Mes projets','Play Others\' Games':'Jeux des autres','NEW PROJECT':'NOUVEAU PROJET','YOUR PROJECTS':'VOS PROJETS','Settings':'Paramètres','Language':'Langue','Close':'Fermer','Log Out':'Déconnexion','+ Create':'+ Créer','Publish':'Publier','Online':'En ligne','Rename':'Renommer','Delete':'Supprimer','Click card to open':'Cliquez pour ouvrir','Name your new game...':'Nom du nouveau jeu...','Edit Manual File':'Modifier le manuel','Reload':'Recharger'},
 'Español':{'My Projects':'Mis proyectos','Play Others\' Games':'Juegos de otros','NEW PROJECT':'NUEVO PROYECTO','YOUR PROJECTS':'TUS PROYECTOS','Settings':'Ajustes','Language':'Idioma','Close':'Cerrar','Log Out':'Salir','+ Create':'+ Crear','Publish':'Publicar','Online':'En línea','Rename':'Renombrar','Delete':'Eliminar','Click card to open':'Haz clic para abrir','Name your new game...':'Nombre de tu juego...','Edit Manual File':'Editar manual','Reload':'Recargar'},
 'Deutsch':{'My Projects':'Meine Projekte','Play Others\' Games':'Spiele anderer','NEW PROJECT':'NEUES PROJEKT','YOUR PROJECTS':'DEINE PROJEKTE','Settings':'Einstellungen','Language':'Sprache','Close':'Schließen','Log Out':'Abmelden','+ Create':'+ Erstellen','Publish':'Veröffentlichen','Online':'Online','Rename':'Umbenennen','Delete':'Löschen','Click card to open':'Zum Öffnen klicken','Name your new game...':'Name des neuen Spiels...','Edit Manual File':'Handbuch bearbeiten','Reload':'Neu laden'},
 '日本語':{'My Projects':'マイプロジェクト','Play Others\' Games':'みんなのゲーム','NEW PROJECT':'新しいプロジェクト','YOUR PROJECTS':'プロジェクト一覧','Settings':'設定','Language':'言語','Close':'閉じる','Log Out':'ログアウト','+ Create':'+ 作成','Publish':'公開','Online':'オンライン','Rename':'名前を変更','Delete':'削除','Click card to open':'クリックして開く','Name your new game...':'ゲーム名を入力...','Edit Manual File':'マニュアルを編集','Reload':'再読み込み'},
 '简体中文':{'My Projects':'我的项目','Play Others\' Games':'玩其他人的游戏','NEW PROJECT':'新建项目','YOUR PROJECTS':'我的项目','Settings':'设置','Language':'语言','Close':'关闭','Log Out':'退出登录','+ Create':'+ 创建','Publish':'发布','Online':'在线','Rename':'重命名','Delete':'删除','Click card to open':'点击打开','Name your new game...':'输入游戏名称...','Edit Manual File':'编辑手册','Reload':'重新加载'},
 '한국어':{'My Projects':'내 프로젝트','Play Others\' Games':'다른 사람의 게임','NEW PROJECT':'새 프로젝트','YOUR PROJECTS':'내 프로젝트','Settings':'설정','Language':'언어','Close':'닫기','Log Out':'로그아웃','+ Create':'+ 만들기','Publish':'게시','Online':'온라인','Rename':'이름 변경','Delete':'삭제','Click card to open':'클릭하여 열기','Name your new game...':'게임 이름 입력...','Edit Manual File':'설명서 편집','Reload':'새로고침'},
}

# Community gallery and publish form translations
for _lang,_vals in {'Français': ['Retour aux projets', 'Jeux communautaires', 'Rechercher par créateur ou titre', 'Rechercher un nom ou un jeu...', 'Jeux locaux', 'Jeux en ligne', 'Actualiser', 'Galerie locale : jeux publiés sur ce Mac', 'Jouer', 'Aucun jeu trouvé. Publiez depuis Mes projets !', 'Configurer Supabase pour voir les jeux publics.', 'par', 'Publier votre jeu', 'Titre', 'Description', 'Image de couverture (chemin facultatif)', 'Tab : champ suivant • Entrée : publier • Échap : annuler', 'Annuler'], 'Español': ['Volver a proyectos', 'Juegos de la comunidad', 'Buscar por creador o título', 'Buscar usuario o juego...', 'Juegos locales', 'Juegos en línea', 'Actualizar', 'Galería local: juegos publicados en este Mac', 'Jugar', 'No hay juegos. ¡Publica desde Mis proyectos!', 'Configura Supabase para ver juegos públicos.', 'por', 'Publicar tu juego', 'Título', 'Descripción', 'Imagen de portada (ruta opcional)', 'Tab: siguiente • Intro: publicar • Esc: cancelar', 'Cancelar'], 'Deutsch': ['Zurück zu Projekten', 'Community-Spiele', 'Nach Ersteller oder Titel suchen', 'Benutzer oder Spiel suchen...', 'Lokale Spiele', 'Online-Spiele', 'Aktualisieren', 'Lokale Galerie: auf diesem Mac veröffentlicht', 'Spielen', 'Keine Spiele gefunden. Veröffentliche eines!', 'Supabase für öffentliche Spiele einrichten.', 'von', 'Spiel veröffentlichen', 'Titel', 'Beschreibung', 'Titelbild (optionaler Pfad)', 'Tab: weiter • Enter: veröffentlichen • Esc: abbrechen', 'Abbrechen'], '日本語': ['プロジェクトに戻る', 'コミュニティゲーム', '作者名またはタイトルで検索', 'ユーザー名またはゲーム名...', 'ローカルゲーム', 'オンラインゲーム', '更新', 'このMacで公開したゲーム', 'プレイ', 'ゲームがありません。プロジェクトから公開しましょう！', '公開ゲームを見るにはSupabaseを設定してください。', '作者', 'ゲームを公開', 'タイトル', '説明', '表紙画像（任意のパス）', 'Tab: 次へ • Enter: 公開 • Esc: キャンセル', 'キャンセル'], '简体中文': ['返回项目', '社区游戏', '按作者用户名或游戏名称搜索', '搜索用户名或游戏标题...', '本地游戏', '在线游戏', '刷新在线游戏', '本地游戏库：此 Mac 上发布的游戏', '开始游戏', '暂无游戏，请从我的项目发布！', '请设置 Supabase 以查看公开游戏。', '作者', '发布你的游戏', '标题', '游戏简介', '封面图片（可选路径）', 'Tab：下一项 • 回车：发布 • Esc：取消', '取消'], '한국어': ['프로젝트로 돌아가기', '커뮤니티 게임', '제작자 또는 제목으로 검색', '사용자 또는 게임 검색...', '로컬 게임', '온라인 게임', '새로고침', '로컬 갤러리: 이 Mac에 게시한 게임', '게임 플레이', '게임이 없습니다. 내 프로젝트에서 게시하세요!', '공개 게임을 보려면 Supabase를 설정하세요.', '제작자', '게임 게시', '제목', '설명', '표지 이미지(선택 경로)', 'Tab: 다음 • Enter: 게시 • Esc: 취소', '취소']}.items():
    UI_TRANSLATIONS[_lang].update(dict(zip(['Back to Projects', 'Community Games', 'Find games by creator username or title', 'Search username or game title...', 'Local Games', 'Online Games', 'Refresh online', 'Local gallery: games published on this Mac', 'Play Game', 'No games found yet. Publish one from My Projects!', 'Set up Supabase to see public games.', 'by', 'Publish your game', 'Title', 'Description', 'Cover image (optional path)', 'Tab: next field  •  Enter: publish  •  Esc: cancel', 'Cancel'],_vals)))
for _lang,_labels in {
 'Français':('Image de couverture (facultatif)','Choisir une image…','Changer l’image…'),
 'Español':('Imagen de portada (opcional)','Elegir imagen…','Cambiar imagen…'),
 'Deutsch':('Titelbild (optional)','Bild auswählen…','Bild ändern…'),
 '日本語':('カバー画像（任意）','画像を選択…','画像を変更…'),
 '简体中文':('封面图片（可选）','选择图片…','更换图片…'),
 '한국어':('표지 이미지 (선택)','이미지 선택…','이미지 변경…'),
}.items():
    UI_TRANSLATIONS[_lang].update(dict(zip(('Cover image (optional)','Choose Image...','Change Image...'),_labels)))



for _lang, _words in {
    'Français': ('Supprimer le jeu', 'Confirmer', 'Cliquez sur Confirmer pour supprimer définitivement ce jeu local.', 'Jeu local supprimé.'),
    'Español': ('Eliminar juego', 'Confirmar', 'Pulsa Confirmar para eliminar este juego local permanentemente.', 'Juego local eliminado.'),
    'Deutsch': ('Spiel löschen', 'Bestätigen', 'Zum endgültigen Löschen dieses lokalen Spiels bestätigen.', 'Lokales Spiel gelöscht.'),
    '日本語': ('ゲームを削除', '削除を確認', 'もう一度押すとローカルゲームが完全に削除されます。', 'ローカルゲームを削除しました。'),
    '简体中文': ('删除游戏', '确认删除', '再次点击确认删除，将永久删除此本地发布的游戏。', '已删除本地发布的游戏。'),
    '한국어': ('게임 삭제', '삭제 확인', '다시 누르면 이 로컬 게임이 영구 삭제됩니다.', '로컬 게시 게임이 삭제되었습니다.'),
}.items():
    UI_TRANSLATIONS[_lang].update(dict(zip(
        ('Delete Game','Confirm Delete','Click Confirm Delete to permanently remove this local published game.','Local published game deleted.'), _words)))

def ui(value):
    return UI_TRANSLATIONS.get(ui_language,{}).get(str(value),str(value))

# v35: Choose a CJK font based on the actual text being drawn, including
# language names shown while English is selected. macOS pygame.match_font()
# may not find Apple's fonts by family name, so check system font files too.
_CJK_FONTS={}
def _cjk_script(value):
    s=str(value)
    if any('\uac00' <= ch <= '\ud7af' or '\u1100' <= ch <= '\u11ff' for ch in s):
        return 'ko'
    if any('\u3040' <= ch <= '\u30ff' for ch in s):
        return 'ja'
    if any('\u3400' <= ch <= '\u9fff' for ch in s):
        return 'ja' if '日本' in s else ('zh' if '简体' in s else ('ko' if '한국' in s else {'日本語':'ja','한국어':'ko'}.get(ui_language,'zh')))
    return None

def interface_font(base, value=''):
    script=_cjk_script(value)
    if script is None:
        return base
    size=max(12,base.get_height()-2)
    key=(script,size)
    if key not in _CJK_FONTS:
        import glob
        families={
            'ja':['hiraginosans','hiragino sans','yugothic','yu gothic','notosanscjk'],
            'zh':['pingfangsc','pingfang sc','heiti sc','notosanscjk'],
            'ko':['applesdgothicneo','apple sd gothic neo','nanumgothic','notosanscjk'],
        }[script]
        paths={
            'ja':['/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc',
                  '/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc',
                  '/System/Library/Fonts/Supplemental/Arial Unicode.ttf'],
            'zh':['/System/Library/Fonts/PingFang.ttc',
                  '/System/Library/Fonts/STHeiti Light.ttc',
                  '/System/Library/Fonts/Supplemental/Arial Unicode.ttf'],
            'ko':['/System/Library/Fonts/AppleSDGothicNeo.ttc',
                  '/System/Library/Fonts/Supplemental/AppleGothic.ttf',
                  '/System/Library/Fonts/Supplemental/Arial Unicode.ttf'],
        }[script]
        paths+=glob.glob('/System/Library/Fonts/**/*Hiragino*',recursive=True) if script=='ja' else []
        paths+=glob.glob('/System/Library/Fonts/**/*PingFang*',recursive=True) if script=='zh' else []
        paths+=glob.glob('/System/Library/Fonts/**/*Gothic*',recursive=True) if script=='ko' else []
        paths+=glob.glob('/usr/share/fonts/**/*NotoSansCJK*',recursive=True)
        paths+=glob.glob('/Library/Fonts/*')
        paths+=glob.glob(os.path.expanduser('~/Library/Fonts/*'))
        paths+=glob.glob('/System/Library/Fonts/Supplemental/*')
        # Some macOS installations keep East Asian fonts in system asset caches.
        paths+=glob.glob('/System/Library/AssetsV2/com_apple_MobileAsset_Font*/**/*.ttc',recursive=True)
        paths+=glob.glob('/System/Library/AssetsV2/com_apple_MobileAsset_Font*/**/*.otf',recursive=True)
        paths+=glob.glob('/System/Library/AssetsV2/com_apple_MobileAsset_Font*/**/*.ttf',recursive=True)
        # Prioritize matching language-specific names over arbitrary fonts.
        hints={'ja':('hiragino','gothic','noto','yu'), 'zh':('pingfang','heiti','noto','song'), 'ko':('gothic','nanum','noto','apple')}[script]
        paths=[p for p in paths if any(h in os.path.basename(p).lower() for h in hints)]
        candidates=[pygame.font.match_font(n) for n in families]+paths
        font=base
        for path in candidates:
            if path and os.path.isfile(path):
                try:
                    font=pygame.font.Font(path,size)
                    break
                except (OSError,pygame.error):
                    continue
        _CJK_FONTS[key]=font
    return _CJK_FONTS[key]

# v37: Compact, tabbed settings. Preferences are stored locally, not in Supabase.
SETTINGS_FILE=os.path.join(os.path.expanduser('~'),'.2d_appmaker_ui_settings.json')
settings_tab='General'
settings_prefs={'language':'English','accent':'Pink','hover':True,'tips':True,'confirm_delete':True}
# Complete coordinated palettes: changing an accent updates the whole interface,
# not only its highlighted labels and button borders.
THEMES={
 'Pink':{'bg':(45,43,51),'panel':(66,61,73),'canvas':(86,82,94),'toolbar':(66,61,73),
         'line':(155,117,143),'text':(255,238,246),'muted':(225,191,209),
         'accent':(246,143,188),'selected':(130,76,112),'green':(255,169,205),'red':(255,128,155)},
 'Peach':{'bg':(47,39,39),'panel':(75,61,61),'canvas':(94,79,79),'toolbar':(75,61,61),
          'line':(194,140,124),'text':(255,241,231),'muted':(241,201,184),
          'accent':(255,177,151),'selected':(137,91,81),'green':(255,194,166),'red':(255,133,126)},
 'Lavender':{'bg':(42,40,53),'panel':(65,60,81),'canvas':(82,78,101),'toolbar':(65,60,81),
             'line':(150,132,189),'text':(245,238,255),'muted':(208,194,235),
             'accent':(188,160,240),'selected':(109,85,147),'green':(207,182,255),'red':(246,135,174)}
}
ACCENTS={name:palette['accent'] for name,palette in THEMES.items()}
def apply_ui_theme(name):
    COL.update(THEMES.get(name,THEMES['Pink']))

def load_ui_preferences():
    global ui_language
    try:
        with open(SETTINGS_FILE,encoding='utf-8') as f:
            data=json.load(f)
        if isinstance(data,dict):
            for key in settings_prefs:
                if key in data and isinstance(data[key],type(settings_prefs[key])):
                    settings_prefs[key]=data[key]
    except (OSError,ValueError,TypeError):pass
    if settings_prefs['language'] in UI_TRANSLATIONS:
        ui_language=settings_prefs['language']
    apply_ui_theme(settings_prefs['accent'])
def save_ui_preferences():
    try:
        with open(SETTINGS_FILE,'w',encoding='utf-8') as f:
            json.dump(settings_prefs,f,ensure_ascii=False,indent=2)
    except OSError:pass
load_ui_preferences()

def settings_rects():
    w,h=screen.get_size()
    modal=pygame.Rect((w-660)//2,(h-490)//2,660,490)
    return modal,pygame.Rect(modal.right-104,modal.y+17,82,34)

def settings_regions():
    modal,close=settings_rects()
    tabs=['General','Appearance','Language','Notifications','Account']
    tab_rects={name:pygame.Rect(modal.x+18,modal.y+89+i*57,176,45) for i,name in enumerate(tabs)}
    content=pygame.Rect(modal.x+217,modal.y+88,modal.w-242,modal.h-112)
    return modal,close,tab_rects,content

def settings_options():
    _,_,_,area=settings_regions()
    x,y,w=area.x+13,area.y+61,area.w-26
    if settings_tab=='Language':
        return [(name,pygame.Rect(x,y+i*42,w,35)) for i,name in enumerate(UI_TRANSLATIONS)]
    if settings_tab=='Appearance':
        return [(name,pygame.Rect(x,y+i*48,w,39)) for i,name in enumerate(ACCENTS)]+[
            ('Hover highlights',pygame.Rect(x,y+167,w,42))]
    if settings_tab=='Notifications':
        return [('Show editor tips',pygame.Rect(x,y,w,42))]
    if settings_tab=='General':
        return [('Confirm project deletion',pygame.Rect(x,y,w,42))]
    if settings_tab=='Account':
        return [('Log Out',pygame.Rect(x,y+112,w,42))]
    return []

def draw_settings():
    shade=pygame.Surface(screen.get_size(),pygame.SRCALPHA)
    shade.fill((12,9,18,175));screen.blit(shade,(0,0))
    modal,close,tabs,area=settings_regions()
    pygame.draw.rect(screen,COL['panel'],modal,border_radius=14)
    pygame.draw.rect(screen,COL['accent'],modal,2,border_radius=14)
    draw_text('Settings',modal.x+22,modal.y+23,COL['accent'],BIG)
    button(close,'Close',small=True)
    pygame.draw.line(screen,COL['line'],(modal.x+204,modal.y+79),(modal.x+204,modal.bottom-19),1)
    for name,rect in tabs.items():
        button(rect,name,selected=settings_tab==name,small=True)
    draw_text(settings_tab,area.x+13,area.y+12,COL['accent'])
    if settings_tab=='General':
        draw_text('Your workspace preferences',area.x+13,area.y+39,COL['muted'],SMALL)
    elif settings_tab=='Appearance':
        draw_text('Accent color',area.x+13,area.y+39,COL['muted'],SMALL)
    elif settings_tab=='Language':
        draw_text('Interface language',area.x+13,area.y+39,COL['muted'],SMALL)
    elif settings_tab=='Notifications':
        draw_text('Help text preferences',area.x+13,area.y+39,COL['muted'],SMALL)
    elif settings_tab=='Account':
        draw_text('Signed in as:',area.x+13,area.y+39,COL['muted'],SMALL)
        draw_text(str(profile_slug or 'Guest')[:26],area.x+13,area.y+68,COL['text'])
    for name,rect in settings_options():
        selected=(settings_tab=='Language' and ui_language==name) or (settings_tab=='Appearance' and settings_prefs['accent']==name)
        button(rect,name,selected=selected,small=True)
        if name=='Hover highlights':value=settings_prefs['hover']
        elif name=='Show editor tips':value=settings_prefs['tips']
        elif name=='Confirm project deletion':value=settings_prefs['confirm_delete']
        else:continue
        mark='ON' if value else 'OFF'
        draw_text(mark,rect.right-47,rect.y+11,COL['accent'] if value else COL['muted'],SMALL)
    if settings_tab=='Language':
        draw_text('Game dialogue is not translated.',area.x+13,area.bottom-17,COL['muted'],SMALL)
    else:
        draw_text('Changes save automatically.',area.x+13,area.bottom-17,COL['muted'],SMALL)

def settings_click(pos):
    global settings_open,ui_language,settings_tab
    modal,close,tabs,area=settings_regions()
    if close.collidepoint(pos) or not modal.collidepoint(pos):
        settings_open=False;return
    for name,rect in tabs.items():
        if rect.collidepoint(pos):settings_tab=name;return
    for name,rect in settings_options():
        if not rect.collidepoint(pos):continue
        if settings_tab=='Language':
            ui_language=name;settings_prefs['language']=name
        elif settings_tab=='Appearance':
            if name in ACCENTS:
                settings_prefs['accent']=name;apply_ui_theme(name)
            elif name=='Hover highlights':settings_prefs['hover']=not settings_prefs['hover']
        elif settings_tab=='Notifications':
            settings_prefs['tips']=not settings_prefs['tips']
        elif settings_tab=='General':
            settings_prefs['confirm_delete']=not settings_prefs['confirm_delete']
        elif settings_tab=='Account' and name=='Log Out':
            settings_open=False;log_out();return
        save_ui_preferences()
        return


# v44: editor-wide interface translations. User-authored content is not translated.
EDITOR_TRANSLATIONS = {
    '简体中文': {'Scenes': '场景', 'Story Flow': '剧情流程', 'Import Character': '导入角色', 'Import Background': '导入背景', 'Import Video': '导入视频', 'Import Text': '导入文字', 'Import Objects': '导入物体', 'Import UI/Buttons': '导入界面/按钮', 'Sounds': '声音', 'Background Music': '背景音乐', 'Overlays': '叠加图层', 'Code Chat': '代码聊天', 'Entity Events': '实体事件', 'Python Script': 'Python 脚本', 'Coding Manual': '代码手册', 'Information': '信息', 'TOOLS': '工具', 'SCENE ENTITIES': '场景实体', 'New Folder': '新建文件夹', 'Move to Folder': '移入文件夹', 'Sound Events': '声音事件', 'Choose Parent →': '选择父级 →', 'Show / Hide': '显示/隐藏', 'Detach': '解除关联', 'Edit / Events': '编辑/事件', 'Run': '运行', 'Clear': '清空', 'Run Python': '运行 Python', 'New Animation': '新建动画', 'Add Frame': '添加帧', 'Next Animation': '下一个动画', 'Play / Pause': '播放/暂停', 'Import Sound': '导入声音', 'Play Sound': '播放声音', 'Stop Sounds': '停止声音', 'New Overlay': '新建叠加层', 'Set Parent (Code Chat)': '设置父级（代码）', 'Detach Selected': '解除选中项关联', 'GAME PREVIEW': '游戏预览', 'EDITOR CANVAS': '编辑画布', 'STORY FLOW': '剧情流程', 'COMMAND CONSOLE': '命令控制台', 'EDIT SELECTED TEXT': '编辑选中文字', 'BACKGROUND MUSIC / AUDIO': '背景音乐/音频', 'Select an entity > Sound Events': '选择实体 > 声音事件', 'No item selected': '未选择项目', 'Select an image item first.': '请先选择图片项目', 'Select a character on the canvas': '请在画布上选择角色', 'Selected:': '已选择：', 'Parent:': '父级：', 'Active:': '当前：', 'Frames:': '帧数：', 'Audio:': '音频：', 'TARGET:': '目标：', 'CURRENT:': '当前：', 'Scenes:': '场景数：', 'Selected: ': '已选择：', 'Parent: ': '父级：', 'Audio: ': '音频：', 'Click > to preview': '点击 > 试听', 'Saved commands:': '已保存的命令：', 'Select item, then Code Chat:': '选择项目，然后打开代码聊天：', 'Select child, then attach to parent.': '选择子对象，再绑定父级', 'Overlay images render above characters.': '叠加图片显示在角色上方', 'Type command...': '输入命令...', 'Type your world text here...': '在此输入文字...', 'Create a new scene': '创建新场景', 'Scene name': '场景名称', 'New named animation': '新建命名动画', 'Enter to create  /  Esc to cancel': '回车创建 / Esc 取消', 'Settings': '设置', 'General': '通用', 'Appearance': '外观', 'Notifications': '通知', 'Account': '账户', 'Language': '语言', 'Close': '关闭', 'Interface language': '界面语言', 'Game dialogue is not translated.': '游戏对话不会被翻译。', 'Your workspace preferences': '工作区偏好设置', 'Accent color': '强调色', 'Help text preferences': '提示文字设置', 'Signed in as:': '当前登录：', 'Changes save automatically.': '更改会自动保存。', 'PROJECT': '项目', 'LOCAL': '本地', 'ONLINE': '在线', 'Welcome, ': '欢迎，', 'No projects yet. Create your first game above!': '还没有项目。请先创建游戏！', 'SCENE ENTITIES  ≡  drag to move': '场景实体 ≡ 拖动移动', 'Edit Manual File': '编辑手册', 'Reload': '重新加载', 'Add Sound': '添加声音', 'Add Overlay': '添加叠加层', 'Choose Parent': '选择父级'},
    '日本語': {'Scenes': 'シーン', 'Story Flow': 'ストーリーの流れ', 'Import Character': 'キャラを読み込む', 'Import Background': '背景を読み込む', 'Import Video': '動画を読み込む', 'Import Text': 'テキストを追加', 'Import Objects': 'オブジェクトを読み込む', 'Import UI/Buttons': 'UI/ボタンを読み込む', 'Sounds': 'サウンド', 'Background Music': 'BGM', 'Overlays': 'オーバーレイ', 'Code Chat': 'コードチャット', 'Entity Events': 'エンティティイベント', 'Python Script': 'Python スクリプト', 'Coding Manual': 'コードマニュアル', 'Information': '情報', 'TOOLS': 'ツール', 'SCENE ENTITIES': 'シーンのエンティティ', 'New Folder': '新規フォルダー', 'Move to Folder': 'フォルダーに移動', 'Sound Events': 'サウンドイベント', 'Choose Parent →': '親を選択 →', 'Show / Hide': '表示/非表示', 'Detach': '親子関係を解除', 'Edit / Events': '編集/イベント', 'Run': '実行', 'Clear': 'クリア', 'Run Python': 'Python 実行', 'New Animation': '新規アニメーション', 'Add Frame': 'フレーム追加', 'Next Animation': '次のアニメーション', 'Play / Pause': '再生/一時停止', 'Import Sound': '音声を読み込む', 'Play Sound': '音声を再生', 'Stop Sounds': '音声を停止', 'New Overlay': '新規オーバーレイ', 'Set Parent (Code Chat)': '親を設定（コード）', 'Detach Selected': '選択を解除', 'GAME PREVIEW': 'ゲームプレビュー', 'EDITOR CANVAS': '編集キャンバス', 'STORY FLOW': 'ストーリーの流れ', 'COMMAND CONSOLE': 'コマンドコンソール', 'EDIT SELECTED TEXT': '選択した文字を編集', 'BACKGROUND MUSIC / AUDIO': 'BGM/音声', 'No item selected': '未選択', 'Select an image item first.': '画像を選択してください', 'Select a character on the canvas': 'キャンバスのキャラを選択', 'Selected:': '選択：', 'Parent:': '親：', 'Active:': '使用中：', 'Frames:': 'フレーム：', 'Audio:': '音声：', 'TARGET:': '対象：', 'CURRENT:': '現在：', 'Scenes:': 'シーン：', 'Click > to preview': '> をクリックして試聴', 'Saved commands:': '保存済みコマンド：', 'Type command...': 'コマンドを入力...', 'Create a new scene': '新しいシーンを作成', 'Scene name': 'シーン名', 'New named animation': 'アニメーションを作成', 'General': '一般', 'Appearance': '外観', 'Notifications': '通知', 'Account': 'アカウント', 'Interface language': '表示言語', 'Game dialogue is not translated.': 'ゲーム内の会話は翻訳されません。', 'Your workspace preferences': 'ワークスペースの設定', 'Accent color': 'アクセントカラー', 'Help text preferences': 'ヘルプの設定', 'Signed in as:': 'ログイン中：', 'Changes save automatically.': '変更は自動保存されます。', 'PROJECT': 'プロジェクト', 'LOCAL': 'ローカル', 'ONLINE': 'オンライン', 'SCENE ENTITIES  ≡  drag to move': 'シーンのエンティティ ≡ ドラッグで移動'},
    '한국어': {'Scenes': '장면', 'Story Flow': '스토리 흐름', 'Import Character': '캐릭터 가져오기', 'Import Background': '배경 가져오기', 'Import Video': '동영상 가져오기', 'Import Text': '텍스트 추가', 'Import Objects': '오브젝트 가져오기', 'Import UI/Buttons': 'UI/버튼 가져오기', 'Sounds': '소리', 'Background Music': '배경 음악', 'Overlays': '오버레이', 'Code Chat': '코드 채팅', 'Entity Events': '개체 이벤트', 'Python Script': 'Python 스크립트', 'Coding Manual': '코딩 설명서', 'Information': '정보', 'TOOLS': '도구', 'SCENE ENTITIES': '장면 개체', 'New Folder': '새 폴더', 'Move to Folder': '폴더로 이동', 'Sound Events': '사운드 이벤트', 'Choose Parent →': '부모 선택 →', 'Show / Hide': '표시/숨기기', 'Detach': '연결 해제', 'Edit / Events': '편집/이벤트', 'Run': '실행', 'Clear': '지우기', 'Run Python': 'Python 실행', 'New Animation': '새 애니메이션', 'Add Frame': '프레임 추가', 'Next Animation': '다음 애니메이션', 'Play / Pause': '재생/일시 정지', 'Import Sound': '사운드 가져오기', 'Play Sound': '사운드 재생', 'Stop Sounds': '사운드 중지', 'New Overlay': '새 오버레이', 'Set Parent (Code Chat)': '부모 설정(코드)', 'Detach Selected': '선택 연결 해제', 'GAME PREVIEW': '게임 미리보기', 'EDITOR CANVAS': '편집 캔버스', 'STORY FLOW': '스토리 흐름', 'COMMAND CONSOLE': '명령 콘솔', 'EDIT SELECTED TEXT': '선택한 텍스트 편집', 'BACKGROUND MUSIC / AUDIO': '배경 음악/오디오', 'No item selected': '선택된 항목 없음', 'Select an image item first.': '이미지를 먼저 선택하세요', 'Select a character on the canvas': '캔버스에서 캐릭터를 선택하세요', 'Selected:': '선택:', 'Parent:': '부모:', 'Active:': '활성:', 'Frames:': '프레임:', 'Audio:': '오디오:', 'TARGET:': '대상:', 'CURRENT:': '현재:', 'Scenes:': '장면:', 'Click > to preview': '> 클릭하여 미리 듣기', 'Saved commands:': '저장된 명령:', 'Type command...': '명령 입력...', 'Create a new scene': '새 장면 만들기', 'Scene name': '장면 이름', 'New named animation': '새 애니메이션 만들기', 'General': '일반', 'Appearance': '모양', 'Notifications': '알림', 'Account': '계정', 'Interface language': '인터페이스 언어', 'Game dialogue is not translated.': '게임 대사는 번역되지 않습니다.', 'Your workspace preferences': '작업 공간 설정', 'Accent color': '강조 색상', 'Help text preferences': '도움말 설정', 'Signed in as:': '로그인 계정:', 'Changes save automatically.': '변경 사항이 자동 저장됩니다.', 'PROJECT': '프로젝트', 'LOCAL': '로컬', 'ONLINE': '온라인', 'SCENE ENTITIES  ≡  drag to move': '장면 개체 ≡ 드래그하여 이동'},
    'Français': {'Scenes': 'Scènes', 'Story Flow': 'Parcours narratif', 'Import Character': 'Importer un personnage', 'Import Background': 'Importer un fond', 'Import Video': 'Importer une vidéo', 'Import Text': 'Ajouter du texte', 'Import Objects': 'Importer des objets', 'Import UI/Buttons': 'Importer UI/boutons', 'Sounds': 'Sons', 'Background Music': 'Musique de fond', 'Overlays': 'Calques', 'Code Chat': 'Chat de code', 'Entity Events': 'Événements des entités', 'Python Script': 'Script Python', 'Coding Manual': 'Manuel de code', 'Information': 'Informations', 'TOOLS': 'OUTILS', 'SCENE ENTITIES': 'ENTITÉS DE LA SCÈNE', 'New Folder': 'Nouveau dossier', 'Move to Folder': 'Déplacer au dossier', 'Sound Events': 'Événements audio', 'Choose Parent →': 'Choisir un parent →', 'Show / Hide': 'Afficher / masquer', 'Detach': 'Détacher', 'Edit / Events': 'Modifier / événements', 'Run': 'Exécuter', 'Clear': 'Effacer', 'Run Python': 'Exécuter Python', 'New Animation': 'Nouvelle animation', 'Add Frame': 'Ajouter une image', 'Next Animation': 'Animation suivante', 'Play / Pause': 'Lire / Pause', 'Import Sound': 'Importer un son', 'Play Sound': 'Lire le son', 'Stop Sounds': 'Arrêter les sons', 'New Overlay': 'Nouveau calque', 'Set Parent (Code Chat)': 'Définir le parent', 'Detach Selected': 'Détacher la sélection', 'GAME PREVIEW': 'APERÇU DU JEU', 'EDITOR CANVAS': "ZONE D'ÉDITION", 'STORY FLOW': 'PARCOURS NARRATIF', 'COMMAND CONSOLE': 'CONSOLE DE COMMANDES', 'EDIT SELECTED TEXT': 'MODIFIER LE TEXTE', 'BACKGROUND MUSIC / AUDIO': 'MUSIQUE DE FOND / AUDIO', 'No item selected': 'Aucun élément sélectionné', 'Selected:': 'Sélection :', 'Parent:': 'Parent :', 'Active:': 'Actif :', 'Frames:': 'Images :', 'Audio:': 'Audio :', 'TARGET:': 'CIBLE :', 'CURRENT:': 'ACTUEL :', 'Scenes:': 'Scènes :', 'Click > to preview': 'Cliquez sur > pour écouter', 'Saved commands:': 'Commandes enregistrées :', 'Type command...': 'Saisir une commande...', 'Create a new scene': 'Créer une scène', 'Scene name': 'Nom de la scène', 'New named animation': 'Nouvelle animation nommée', 'General': 'Général', 'Appearance': 'Apparence', 'Notifications': 'Notifications', 'Account': 'Compte', 'Interface language': "Langue de l'interface", 'Game dialogue is not translated.': 'Les dialogues du jeu ne sont pas traduits.', 'PROJECT': 'PROJET', 'LOCAL': 'LOCAL', 'ONLINE': 'EN LIGNE'},
    'Español': {'Scenes': 'Escenas', 'Story Flow': 'Flujo narrativo', 'Import Character': 'Importar personaje', 'Import Background': 'Importar fondo', 'Import Video': 'Importar vídeo', 'Import Text': 'Agregar texto', 'Import Objects': 'Importar objetos', 'Import UI/Buttons': 'Importar UI/botones', 'Sounds': 'Sonidos', 'Background Music': 'Música de fondo', 'Overlays': 'Capas', 'Code Chat': 'Chat de código', 'Entity Events': 'Eventos de entidades', 'Python Script': 'Script Python', 'Coding Manual': 'Manual de código', 'Information': 'Información', 'TOOLS': 'HERRAMIENTAS', 'SCENE ENTITIES': 'ENTIDADES DE ESCENA', 'New Folder': 'Nueva carpeta', 'Move to Folder': 'Mover a carpeta', 'Sound Events': 'Eventos de sonido', 'Choose Parent →': 'Elegir padre →', 'Show / Hide': 'Mostrar / ocultar', 'Detach': 'Desvincular', 'Edit / Events': 'Editar / eventos', 'Run': 'Ejecutar', 'Clear': 'Borrar', 'Run Python': 'Ejecutar Python', 'New Animation': 'Nueva animación', 'Add Frame': 'Agregar fotograma', 'Next Animation': 'Siguiente animación', 'Play / Pause': 'Reproducir / pausar', 'Import Sound': 'Importar sonido', 'Play Sound': 'Reproducir sonido', 'Stop Sounds': 'Detener sonidos', 'New Overlay': 'Nueva capa', 'Set Parent (Code Chat)': 'Asignar padre', 'Detach Selected': 'Desvincular selección', 'GAME PREVIEW': 'VISTA PREVIA', 'EDITOR CANVAS': 'LIENZO DE EDICIÓN', 'STORY FLOW': 'FLUJO NARRATIVO', 'COMMAND CONSOLE': 'CONSOLA DE COMANDOS', 'EDIT SELECTED TEXT': 'EDITAR TEXTO', 'BACKGROUND MUSIC / AUDIO': 'MÚSICA DE FONDO / AUDIO', 'No item selected': 'Ningún elemento seleccionado', 'Selected:': 'Seleccionado:', 'Parent:': 'Padre:', 'Active:': 'Activo:', 'Frames:': 'Fotogramas:', 'Audio:': 'Audio:', 'TARGET:': 'OBJETIVO:', 'CURRENT:': 'ACTUAL:', 'Scenes:': 'Escenas:', 'Click > to preview': 'Haz clic en > para escuchar', 'Saved commands:': 'Comandos guardados:', 'Type command...': 'Escribe un comando...', 'Create a new scene': 'Crear una escena', 'Scene name': 'Nombre de la escena', 'New named animation': 'Nueva animación', 'General': 'General', 'Appearance': 'Apariencia', 'Notifications': 'Notificaciones', 'Account': 'Cuenta', 'Interface language': 'Idioma de la interfaz', 'Game dialogue is not translated.': 'Los diálogos del juego no se traducen.', 'PROJECT': 'PROYECTO', 'LOCAL': 'LOCAL', 'ONLINE': 'EN LÍNEA'},
    'Deutsch': {'Scenes': 'Szenen', 'Story Flow': 'Handlungsverlauf', 'Import Character': 'Figur importieren', 'Import Background': 'Hintergrund importieren', 'Import Video': 'Video importieren', 'Import Text': 'Text hinzufügen', 'Import Objects': 'Objekte importieren', 'Import UI/Buttons': 'UI/Schaltflächen importieren', 'Sounds': 'Töne', 'Background Music': 'Hintergrundmusik', 'Overlays': 'Überlagerungen', 'Code Chat': 'Code-Chat', 'Entity Events': 'Objektereignisse', 'Python Script': 'Python-Skript', 'Coding Manual': 'Code-Handbuch', 'Information': 'Informationen', 'TOOLS': 'WERKZEUGE', 'SCENE ENTITIES': 'SZENENOBJEKTE', 'New Folder': 'Neuer Ordner', 'Move to Folder': 'In Ordner verschieben', 'Sound Events': 'Tonereignisse', 'Choose Parent →': 'Übergeordnetes wählen →', 'Show / Hide': 'Ein-/Ausblenden', 'Detach': 'Lösen', 'Edit / Events': 'Bearbeiten / Ereignisse', 'Run': 'Ausführen', 'Clear': 'Löschen', 'Run Python': 'Python ausführen', 'New Animation': 'Neue Animation', 'Add Frame': 'Bild hinzufügen', 'Next Animation': 'Nächste Animation', 'Play / Pause': 'Abspielen / Pause', 'Import Sound': 'Ton importieren', 'Play Sound': 'Ton abspielen', 'Stop Sounds': 'Töne stoppen', 'New Overlay': 'Neue Überlagerung', 'Set Parent (Code Chat)': 'Übergeordnetes festlegen', 'Detach Selected': 'Auswahl lösen', 'GAME PREVIEW': 'SPIELVORSCHAU', 'EDITOR CANVAS': 'EDITOR-LEINWAND', 'STORY FLOW': 'HANDLUNGSVERLAUF', 'COMMAND CONSOLE': 'BEFEHLSKONSOLE', 'EDIT SELECTED TEXT': 'TEXT BEARBEITEN', 'BACKGROUND MUSIC / AUDIO': 'HINTERGRUNDMUSIK / AUDIO', 'No item selected': 'Nichts ausgewählt', 'Selected:': 'Ausgewählt:', 'Parent:': 'Übergeordnet:', 'Active:': 'Aktiv:', 'Frames:': 'Bilder:', 'Audio:': 'Audio:', 'TARGET:': 'ZIEL:', 'CURRENT:': 'AKTUELL:', 'Scenes:': 'Szenen:', 'Click > to preview': '> zum Anhören klicken', 'Saved commands:': 'Gespeicherte Befehle:', 'Type command...': 'Befehl eingeben...', 'Create a new scene': 'Neue Szene erstellen', 'Scene name': 'Szenenname', 'New named animation': 'Neue benannte Animation', 'General': 'Allgemein', 'Appearance': 'Darstellung', 'Notifications': 'Benachrichtigungen', 'Account': 'Konto', 'Interface language': 'Oberflächensprache', 'Game dialogue is not translated.': 'Spieldialoge werden nicht übersetzt.', 'PROJECT': 'PROJEKT', 'LOCAL': 'LOKAL', 'ONLINE': 'ONLINE'},
}

# v46: Translate the complete editor control labels, including dynamic scene controls.
SCENE_CONTROL_TRANSLATIONS = {
 '简体中文': {'+ New Scene':'＋ 新建场景','Next Scene':'下一个场景','Type:':'类型：','Walking:':'行走：','Player:':'玩家：','Set dialogue from Code Chat':'从代码聊天设置对话','Add choice via Code Chat':'通过代码聊天添加选项','Collisions:':'碰撞：','Dialogue lines:':'对话行数：','Select character':'选择角色','ON':'开','OFF':'关','drag to move':'拖动移动','drag green corner to resize':'拖动绿色角点调整大小'},
 '日本語': {'+ New Scene':'＋ 新しいシーン','Next Scene':'次のシーン','Type:':'種類：','Walking:':'移動：','Player:':'プレイヤー：','Set dialogue from Code Chat':'コードチャットで会話を設定','Add choice via Code Chat':'コードチャットで選択肢を追加','Collisions:':'衝突：','Dialogue lines:':'会話行数：','Select character':'キャラを選択','ON':'オン','OFF':'オフ'},
 '한국어': {'+ New Scene':'＋ 새 장면','Next Scene':'다음 장면','Type:':'유형:','Walking:':'이동:','Player:':'플레이어:','Set dialogue from Code Chat':'코드 채팅에서 대화 설정','Add choice via Code Chat':'코드 채팅에서 선택지 추가','Collisions:':'충돌:','Dialogue lines:':'대화 줄:','Select character':'캐릭터 선택','ON':'켜짐','OFF':'꺼짐'},
 'Français': {'+ New Scene':'＋ Nouvelle scène','Next Scene':'Scène suivante','Type:':'Type :','Walking:':'Déplacement :','Player:':'Joueur :','Set dialogue from Code Chat':'Définir le dialogue via Code Chat','Add choice via Code Chat':'Ajouter un choix via Code Chat','Collisions:':'Collisions :','Dialogue lines:':'Lignes de dialogue :','Select character':'Choisir un personnage','ON':'ACTIF','OFF':'INACTIF'},
 'Español': {'+ New Scene':'＋ Nueva escena','Next Scene':'Escena siguiente','Type:':'Tipo:','Walking:':'Movimiento:','Player:':'Jugador:','Set dialogue from Code Chat':'Configurar diálogo desde Code Chat','Add choice via Code Chat':'Añadir opción desde Code Chat','Collisions:':'Colisiones:','Líneas de diálogo:':'Líneas de diálogo:','Dialogue lines:':'Líneas de diálogo:','Select character':'Seleccionar personaje','ON':'ACTIVO','OFF':'INACTIVO'},
 'Deutsch': {'+ New Scene':'＋ Neue Szene','Next Scene':'Nächste Szene','Type:':'Typ:','Walking:':'Bewegung:','Player:':'Spieler:','Set dialogue from Code Chat':'Dialog über Code-Chat festlegen','Add choice via Code Chat':'Auswahl über Code-Chat hinzufügen','Collisions:':'Kollisionen:','Dialogue lines:':'Dialogzeilen:','Select character':'Figur auswählen','ON':'AN','OFF':'AUS'}
}

ADDITIONAL_UI_TRANSLATIONS = {'简体中文': {'Import': '导入', 'Files': '文件', 'Edit': '编辑', 'Game Mode': '游戏模式', 'Animation': '动画', 'Entities': '实体', 'Pink': '粉色', 'Peach': '蜜桃色', 'Lavender': '薰衣草紫', 'Hover highlights': '悬停高亮', 'Your cozy little game studio': '温馨的小游戏工作室', 'Show editor tips': '显示编辑器提示', 'Confirm project deletion': '删除项目前确认'}, '日本語': {'Import': '読み込み', 'Files': 'ファイル', 'Edit': '編集', 'Game Mode': 'ゲームモード', 'Animation': 'アニメーション', 'Entities': 'エンティティ', 'Pink': 'ピンク', 'Peach': 'ピーチ', 'Lavender': 'ラベンダー', 'Hover highlights': 'ホバー時に強調', 'Your cozy little game studio': '小さくて居心地のよいゲーム工房', 'Show editor tips': 'ヒントを表示', 'Confirm project deletion': '削除前に確認'}, '한국어': {'Import': '가져오기', 'Files': '파일', 'Edit': '편집', 'Game Mode': '게임 모드', 'Animation': '애니메이션', 'Entities': '개체', 'Pink': '분홍색', 'Peach': '복숭아색', 'Lavender': '라벤더', 'Hover highlights': '마우스 올리면 강조', 'Your cozy little game studio': '아늑한 나만의 게임 스튜디오', 'Show editor tips': '편집기 도움말 표시', 'Confirm project deletion': '프로젝트 삭제 확인'}, 'Français': {'Import': 'Importer', 'Files': 'Fichiers', 'Edit': 'Modifier', 'Game Mode': 'Mode jeu', 'Animation': 'Animation', 'Entities': 'Entités', 'Pink': 'Rose', 'Peach': 'Pêche', 'Lavender': 'Lavande', 'Hover highlights': 'Surbrillance au survol', 'Your cozy little game studio': 'Votre petit studio de jeux', 'Show editor tips': 'Afficher les astuces', 'Confirm project deletion': 'Confirmer la suppression'}, 'Español': {'Import': 'Importar', 'Files': 'Archivos', 'Edit': 'Editar', 'Game Mode': 'Modo juego', 'Animation': 'Animación', 'Entities': 'Entidades', 'Pink': 'Rosa', 'Peach': 'Melocotón', 'Lavender': 'Lavanda', 'Hover highlights': 'Resaltar al pasar el cursor', 'Your cozy little game studio': 'Tu acogedor estudio de juegos', 'Show editor tips': 'Mostrar consejos', 'Confirm project deletion': 'Confirmar eliminación'}, 'Deutsch': {'Import': 'Importieren', 'Files': 'Dateien', 'Edit': 'Bearbeiten', 'Game Mode': 'Spielmodus', 'Animation': 'Animation', 'Entities': 'Objekte', 'Pink': 'Rosa', 'Peach': 'Pfirsich', 'Lavender': 'Lavendel', 'Hover highlights': 'Beim Darüberfahren hervorheben', 'Your cozy little game studio': 'Dein gemütliches Spielestudio', 'Show editor tips': 'Editor-Tipps anzeigen', 'Confirm project deletion': 'Löschen bestätigen'}}


ENTITY_FOLDER_TRANSLATIONS = {'简体中文': {'[DRAG HERE]': '拖动面板', 'entities count': '个实体', 'audio count': '音频', 'folders count': '个文件夹', '+ New Folder': '＋ 新建文件夹', 'Move to Folder': '移至文件夹', 'Ungrouped': '未分组', 'Click > to preview': '点击 > 试听', 'Create entity folder': '创建实体文件夹', 'Folder name...': '文件夹名称...', 'Enter: create  ·  Esc: cancel': '回车：创建 · Esc：取消', 'Code Chat': '代码聊天', 'Choose Parent →': '选择父对象 →', 'Show / Hide': '显示 / 隐藏', 'Detach': '解除关联', 'Sound Events': '音效事件'}, '日本語': {'[DRAG HERE]': 'パネルをドラッグ', 'entities count': 'エンティティ', 'audio count': '音声', 'folders count': 'フォルダー', '+ New Folder': '＋ 新しいフォルダー', 'Move to Folder': 'フォルダーへ移動', 'Ungrouped': '未分類', 'Click > to preview': '> で試聴', 'Create entity folder': 'フォルダーを作成', 'Folder name...': 'フォルダー名...', 'Enter: create  ·  Esc: cancel': 'Enter：作成 · Esc：取消', 'Code Chat': 'コードチャット', 'Choose Parent →': '親を選択 →', 'Show / Hide': '表示 / 非表示', 'Detach': '切り離す', 'Sound Events': '音声イベント'}, '한국어': {'[DRAG HERE]': '패널 끌기', 'entities count': '개체', 'audio count': '오디오', 'folders count': '폴더', '+ New Folder': '＋ 새 폴더', 'Move to Folder': '폴더로 이동', 'Ungrouped': '분류 안 됨', 'Click > to preview': '> 클릭하여 미리 듣기', 'Create entity folder': '개체 폴더 만들기', 'Folder name...': '폴더 이름...', 'Enter: create  ·  Esc: cancel': 'Enter: 만들기 · Esc: 취소', 'Code Chat': '코드 채팅', 'Choose Parent →': '부모 선택 →', 'Show / Hide': '표시 / 숨기기', 'Detach': '연결 해제', 'Sound Events': '소리 이벤트'}, 'Français': {'[DRAG HERE]': 'Déplacer le panneau', 'entities count': 'entités', 'audio count': 'sons', 'folders count': 'dossiers', '+ New Folder': '＋ Nouveau dossier', 'Move to Folder': 'Déplacer au dossier', 'Ungrouped': 'Sans dossier', 'Click > to preview': '> pour écouter', 'Create entity folder': 'Créer un dossier', 'Folder name...': 'Nom du dossier...', 'Enter: create  ·  Esc: cancel': 'Entrée : créer · Échap : annuler', 'Code Chat': 'Code Chat', 'Choose Parent →': 'Choisir parent →', 'Show / Hide': 'Afficher / Masquer', 'Detach': 'Détacher', 'Sound Events': 'Événements audio'}, 'Español': {'[DRAG HERE]': 'Arrastrar panel', 'entities count': 'entidades', 'audio count': 'audios', 'folders count': 'carpetas', '+ New Folder': '＋ Nueva carpeta', 'Move to Folder': 'Mover a carpeta', 'Ungrouped': 'Sin carpeta', 'Click > to preview': '> para escuchar', 'Create entity folder': 'Crear carpeta', 'Folder name...': 'Nombre de carpeta...', 'Enter: create  ·  Esc: cancel': 'Enter: crear · Esc: cancelar', 'Code Chat': 'Chat de código', 'Choose Parent →': 'Elegir padre →', 'Show / Hide': 'Mostrar / Ocultar', 'Detach': 'Separar', 'Sound Events': 'Eventos de sonido'}, 'Deutsch': {'[DRAG HERE]': 'Panel ziehen', 'entities count': 'Objekte', 'audio count': 'Audios', 'folders count': 'Ordner', '+ New Folder': '＋ Neuer Ordner', 'Move to Folder': 'In Ordner verschieben', 'Ungrouped': 'Ohne Ordner', 'Click > to preview': '> zum Anhören', 'Create entity folder': 'Ordner erstellen', 'Folder name...': 'Ordnername...', 'Enter: create  ·  Esc: cancel': 'Enter: erstellen · Esc: abbrechen', 'Code Chat': 'Code-Chat', 'Choose Parent →': 'Übergeordnetes wählen →', 'Show / Hide': 'Anzeigen / Verbergen', 'Detach': 'Trennen', 'Sound Events': 'Tonereignisse'}}

def editor_ui(value):
    value=str(value)
    lookup={**EDITOR_TRANSLATIONS.get(ui_language,{}), **SCENE_CONTROL_TRANSLATIONS.get(ui_language,{}), **ADDITIONAL_UI_TRANSLATIONS.get(ui_language,{}), **ENTITY_FOLDER_TRANSLATIONS.get(ui_language,{})}
    if value in lookup: return lookup[value]
    if value in UI_TRANSLATIONS.get(ui_language,{}): return ui(value)
    for prefix in ("Selected: ","Parent: ","Audio: ","Active: ","Frames: ","Scenes: ","CURRENT: ","TARGET: "):
        if value.startswith(prefix) and prefix.rstrip() in lookup:
            return lookup[prefix.rstrip()]+" "+value[len(prefix):]
    # Translate compound labels without translating user-authored content.
    if value.startswith('EDITOR CANVAS'):
        return value.replace('EDITOR CANVAS',lookup.get('EDITOR CANVAS','EDITOR CANVAS'),1)
    # Dynamic labels: translate only the UI prefix; preserve scene and character names.
    for prefix in ('Type:', 'Walking:', 'Player:', 'Collisions:', 'Dialogue lines:'):
        if value.startswith(prefix):
            suffix=value[len(prefix):].strip()
            if suffix in ('ON','OFF','Select character'):
                suffix=lookup.get(suffix,suffix)
            return lookup.get(prefix,prefix)+' '+suffix
    return value

def draw_text(s, x, y, color=None, font=None):
    s=editor_ui(s)
    font=interface_font(font or FONT,s)
    screen.blit(font.render(display_safe(s),True,color or COL['text']),(int(x),int(y)))

def button(rect,label,selected=False,small=False):
    hovered=settings_prefs.get('hover',True) and rect.collidepoint(pygame.mouse.get_pos()) and pygame.mouse.get_focused()
    # Stable hover state: no flashing/pulsing underline.
    fill=COL['selected'] if selected or hovered else COL['panel']
    pygame.draw.rect(screen,fill,rect,border_radius=6)
    pygame.draw.rect(screen,COL['accent'] if hovered else COL['line'],rect,2 if hovered else 1,border_radius=6)
    translated=editor_ui(label)
    f=interface_font(SMALL if small else FONT,translated)
    surf=f.render(display_safe(translated),True,COL['text'])
    screen.blit(surf,surf.get_rect(center=rect.center))

def layout():
    ww,hh=screen.get_size()
    side=max(265,min(335,ww//4))
    return pygame.Rect(16,92,ww-side-42,hh-110),pygame.Rect(ww-side-16,92,side,hh-110)

def canvas_rect():
    return layout()[0].inflate(-20,-20)

def new_item(kind, path=None, text=None):
    global next_id,selected_id,message
    img=None
    if path:
        try: img=pygame.image.load(path).convert_alpha()
        except (pygame.error,OSError) as e:
            message='Could not load image: '+str(e); return
    cw,ch=map_width,map_height
    if kind=='character':
        w,h=72,72
        name='Character '+str(next_id)
    elif kind=='object':
        w,h=90,90
        name='Object '+str(sum(1 for a in items if a['kind']=='object')+1)
    elif kind=='button':
        w,h=150,44
        name='Button '+str(next_id)
    else:
        w,h=220,45
        name='Text '+str(next_id)
    item={'id':next_id,'kind':kind,'name':name,'x':min(70,cw//5),'y':min(70,ch//5),'w':w,'h':h,
          'path':path,'image':img,'text':text or ('Click me' if kind=='button' else 'Your text here'),
          'frames':[],'animations':{},'active_animation':None,'frame':0,'visible':True,'events':{},'health':100,'parent_id':None,'overlay':False}
    items.append(item); selected_id=next_id; next_id+=1
    message=name+' added. Drag it on the canvas; resize using the corner handle.'

def get_selected():
    return next((i for i in items if i['id']==selected_id),None)

def view_transform(canvas):
    if mode=='play':
        return 1.0,-play_camera[0],-play_camera[1]
    return editor_zoom,editor_pan[0],editor_pan[1]

def world_to_screen(x,y,canvas):
    z,ox,oy=view_transform(canvas)
    return canvas.x+(x+ox)*z,canvas.y+(y+oy)*z

def screen_to_world(x,y,canvas):
    z,ox,oy=view_transform(canvas)
    return (x-canvas.x)/z-ox,(y-canvas.y)/z-oy

def rect_for(i,canvas):
    x,y=item_world_position(i)
    z,_,_=view_transform(canvas)
    sx,sy=world_to_screen(x,y,canvas)
    return pygame.Rect(round(sx),round(sy),max(1,round(i['w']*z)),max(1,round(i['h']*z)))

def update_play_camera():
    if mode!='play':return
    player=next((i for i in items if i.get('id')==player_id and i.get('kind')=='character'),None)
    if player is None:player=next((i for i in items if i.get('kind')=='character'),None)
    if not player:return
    canvas=canvas_rect();x,y=item_world_position(player)
    target_x=max(0,min(max(0,map_width-canvas.w),x+player['w']/2-canvas.w/2))
    target_y=max(0,min(max(0,map_height-canvas.h),y+player['h']/2-canvas.h/2))
    play_camera[0]+=(target_x-play_camera[0])*0.14
    play_camera[1]+=(target_y-play_camera[1])*0.14


def import_background():
    global background,bg_path,bg_rect,message
    p=choose_file('Choose background PNG')
    if p:
        try:
            background=pygame.image.load(p).convert_alpha()
            bg_path=p
            bg_rect=[0,0,background.get_width(),background.get_height()]
            message='Background imported. Use Background Size to adjust it.'
        except (pygame.error,OSError) as e: message='Background error: '+str(e)

def import_video():
    global video_path,message
    p=choose_file('Choose a video file (stored as an asset)')
    if p:
        video_path=p
        message='Video asset linked: '+os.path.basename(p)+' (playback not implemented yet)'

def save_project():
    global message
    p=choose_file('Save your 2d appmaker project',save=True)
    if not p:return
    if not p.lower().endswith('.json'):p+='.json'
    store_scene()
    data={'format_version':8,'scenes':scenes,'scene_order':scene_order,
          'current_scene':current_scene,'flags':story_flags,'video':video_path}
    try:
        with open(p,'w',encoding='utf-8') as f:json.dump(data,f,indent=2)
        message='Project saved: '+os.path.basename(p)
    except (OSError,TypeError) as e:message='Save failed: '+str(e)


def load_project():
    global scenes,scene_order,current_scene,story_flags,video_path,message
    p=choose_file('Open a 2d appmaker JSON project','any')
    if not p:return
    try:
        with open(p,encoding='utf-8') as f:data=json.load(f)
        if 'scenes' in data:
            scenes=data['scenes']
            scene_order=[n for n in data.get('scene_order',list(scenes)) if n in scenes]
            if not scene_order:raise ValueError('No scenes found')
            target=data.get('current_scene',scene_order[0])
        else:
            # Backward compatibility with older single-scene projects.
            target='Scene 1'
            scenes={target:{'background':data.get('background'),
                    'bg_rect':data.get('bg_rect',[0,0,800,600]),
                    'items':data.get('items',[]),'type':'exploration',
                    'movement':True,'player_id':None,'dialogue':'','choices':[],'dialogue_lines':[],'collision':True,'events':{}}}
            scene_order=[target]
        story_flags=data.get('flags',{})
        video_path=data.get('video')
        # Force a fresh scene load without overwriting the imported data.
        # Reset editor state before loading; never overwrite loaded scenes with stale editor data.
        current_scene=target
        reset_project()
        scenes=new_scenes;scene_order=new_order;current_scene=target
        story_flags=data.get('flags',{});video_path=data.get('video')
        change_scene(target)
        message='Project loaded: '+os.path.basename(p)
    except (OSError,ValueError,KeyError,TypeError,pygame.error) as e:
        message='Open failed: '+str(e)

def execute(command):
    global message,dialogue,animation_play,selected_id,mode,dialogue_lines,dialogue_index,collision_enabled,typewriter_cps,dialogue_started_ms
    cmd=command.strip()
    if not cmd:return
    chat_history.append('> '+cmd)
    words=cmd.split()
    action=words[0].upper()
    target=None
    try:
        if action=='ON':
            # ON CLICK SAY Hello; ON TICK CHANCE 5 SHOW; ON DEATH DISAPPEAR
            if len(words)<3:raise ValueError('Use ON CLICK SAY Hello or ON TICK CHANCE 5 SHOW')
            ev=words[1].lower()
            remainder=' '.join(words[2:])
            if ev=='scene' and len(words)>3 and words[2].lower() in ('start','end'):
                ev=words[2].lower();remainder=' '.join(words[3:])
            elif ev in ('touch','enter','exit') and len(words)>3:
                ev=ev+':'+words[2].strip('\"\'').lower();remainder=' '.join(words[3:])
            elif ev=='key' and len(words)>4 and words[3].lower() in ('pressed','released'):
                ev='key:'+words[2].strip('\"\'').lower()+':'+words[3].lower();remainder=' '.join(words[4:])
            elif ev=='dialogue' and len(words)>3 and words[2].lower() in ('end','finished'):
                ev='dialogue_end';remainder=' '.join(words[3:])
            elif ev=='choice':
                import shlex
                parts=shlex.split(' '.join(words[2:]))
                if len(parts)<2:raise ValueError('Use ON CHOICE "Yes" SCENE Scene 2')
                ev='choice:'+parts[0].lower();remainder=' '.join(parts[1:])
            elif ev not in ('start','click','tick','death','end'):
                raise ValueError('Events: START, CLICK, TICK, DEATH, DIALOGUE END, SCENE END, CHOICE, KEY')
            owner=get_selected() if get_selected() is not None else None
            scripts=owner.setdefault('events',{}) if owner is not None else scene_events
            scripts.setdefault(ev,[]).append(remainder)
            message='Saved '+ev.upper()+' event for '+(owner['name'] if owner else 'background')
        elif action=='WHEN':
            # Friendly alias for ON CLICK / ON START / ON DEATH / ON TICK.
            if len(words)<4:raise ValueError('WHEN CLICKED SAY Hello!')
            aliases={'CLICKED':'click','STARTS':'start','DIES':'death','TICKS':'tick'}
            ev=aliases.get(words[1].upper())
            if not ev:raise ValueError('WHEN CLICKED / STARTS / DIES / TICKS')
            owner=get_selected()
            scripts=owner.setdefault('events',{}) if owner is not None else scene_events
            scripts.setdefault(ev,[]).append(' '.join(words[2:]))
            message='Saved WHEN '+words[1].upper()+' for '+(owner['name'] if owner else 'background')
        elif action in ('REMOVE','HAS','START','COMPLETE','FAIL','RESET','MULTIPLY','DIVIDE','RANDOM','LEVEL','BRING','SEND','UNPARENT','PREVIOUS','RESTART') or (action=='ADD' and len(words)>1 and words[1].upper() in ('ITEM','GOLD','XP')) or (action=='CLEAR' and len(words)>1 and words[1].upper()=='INVENTORY') or (action=='NEXT' and len(words)>1 and words[1].upper()=='SCENE') or (action=='MOVE' and len(words)>1 and words[1].upper() in ('TO','TOWARD','AWAY')) or (action=='SHOW' and len(words)>1 and words[1].upper()=='QUEST'):
            owner=get_selected() or {'name':'Background','x':0,'y':0,'events':{}}
            def v31_say(value):set_scene_dialogue(value)
            if rpg_v31_action(owner,shlex.split(cmd),items,v31_say,change_scene,story_flags,0):message='Ran: '+cmd
            else:raise ValueError('Unsupported action. See V31_COMMANDS.md')
        elif action=='GOTO':
            change_scene(' '.join(words[1:]))
        elif action=='EVENTS':
            owner=get_selected()
            scripts=owner.get('events',{}) if owner else scene_events
            message='Events: '+str(scripts)[:140]
        elif action=='CLEAREVENTS':
            owner=get_selected()
            if owner:owner['events']={}
            else:scene_events.clear()
            message='Cleared events for '+(owner['name'] if owner else 'background')
        elif action=='ANIM':
            owner=get_selected()
            if not owner or owner['kind'] not in ('character','object','button'):raise ValueError('Select an imported image first')
            if len(words)<3:raise ValueError('ANIM NEW Walk | ANIM SELECT Walk | ANIM FPS 12 | ANIM LOOP OFF')
            verb=words[1].upper();value=' '.join(words[2:]).strip()
            if verb=='NEW':create_animation_clip(value)
            elif verb=='SELECT':
                if value not in owner.get('animations',{}):raise ValueError('No clip named '+value)
                owner['active_animation']=value;message='Selected animation '+value
            elif verb=='FPS':
                clip=selected_animation(owner)
                if not clip:raise ValueError('Select an animation first')
                clip['fps']=max(1,min(60,int(value)));message='FPS set to '+str(clip['fps'])
            elif verb=='LOOP':
                clip=selected_animation(owner)
                if not clip:raise ValueError('Select an animation first')
                clip['loop']=value.upper()=='ON';message='Loop '+value
            else:raise ValueError('Use ANIM NEW / SELECT / FPS / LOOP')
        elif action in ('PLAY','TOGGLE','STOP') and len(words)>=2 and words[1].upper()=='ANIMATION':
            owner=get_selected()
            if not owner:raise ValueError('Select an item first')
            if action=='STOP':owner['_anim_playing']=False;message='Animation stopped'
            else:
                name=' '.join(words[2:]).strip() or owner.get('active_animation','')
                play_named_animation(owner,name,action)
                message=action+' animation '+name
        elif action=='SOUND':
            if len(words)<2:raise ValueError('SOUND IMPORT | SOUND PLAY name | SOUND LOOP name | SOUND STOP')
            verb=words[1].upper()
            if verb=='IMPORT':
                path=choose_file('Choose WAV, OGG, or MP3 audio',kind='any')
                if path:
                    if Path(path).suffix.lower() not in ('.wav','.ogg','.mp3'):
                        raise ValueError('Use WAV, OGG, or MP3')
                    name=Path(path).stem
                    scene_sounds[name]=path
                    message='Imported sound '+name
            elif verb=='STOP':
                stop_sound(' '.join(words[2:]).strip() or None)
                message='Audio stopped'
            elif verb in ('PLAY','LOOP'):
                name=' '.join(words[2:]).strip()
                if not name:raise ValueError('SOUND PLAY name')
                play_sound(name,verb=='LOOP')
            else:raise ValueError('SOUND IMPORT / PLAY / LOOP / STOP')
        elif action=='PARENT':
            if len(words)<2:raise ValueError('PARENT Character 1 | PARENT NONE')
            child=get_selected()
            if not child:raise ValueError('Select the child item first')
            name=cmd.split(None,1)[1].strip()
            if name.upper() in ('NONE','OFF','CLEAR'):
                detach_item(child);message='Parent removed'
            else:
                parent=item_by_name(name)
                if not parent:raise ValueError('No item named '+name)
                attach_item(child,parent);message=child['name']+' attached to '+parent['name']
        elif action=='OVERLAY':
            if len(words)<2:raise ValueError('OVERLAY ON | OVERLAY OFF')
            owner=get_selected()
            if not owner:raise ValueError('Select an image item')
            owner['overlay']=words[1].upper()=='ON'
            message='Overlay '+('enabled' if owner['overlay'] else 'disabled')
        elif action=='TEXTCOLOR':
            owner=get_selected()
            if not owner or owner.get('kind') not in ('text','button'):
                raise ValueError('Select an imported text or button item first')
            if len(words)!=2:raise ValueError('TEXTCOLOR #FF99CC')
            parse_hex_color(words[1]);owner['text_color']=words[1]
            message=owner['name']+' text color updated'
        elif action=='TEXTBOX' and len(words)>=2 and words[1].upper()=='IMAGE':
            path=choose_file('Choose dialogue box PNG') if len(words)==2 or words[2].upper()=='IMPORT' else ' '.join(words[2:])
            if path and os.path.isfile(path):
                pygame.image.load(path)
                dialogue_style['image']=path
                message='Dialogue box image set: '+os.path.basename(path)
            elif len(words)>2 and words[2].upper() in ('NONE','OFF','CLEAR'):
                dialogue_style['image']=None;message='Dialogue box image cleared'
            elif path:raise ValueError('Image not found: '+path)
        elif action=='TEXTBOX':
            if len(words)!=3:raise ValueError('TEXTBOX BG #282038 | BORDER #FF99CC | TEXT #FFFFFF | OPACITY 220 | HEIGHT 120')
            prop=words[1].upper();value=words[2]
            if prop in ('BG','BORDER','TEXT'):
                parse_hex_color(value);dialogue_style[prop.lower()]=value
            elif prop=='OPACITY':dialogue_style['opacity']=max(0,min(255,int(value)))
            elif prop=='HEIGHT':dialogue_style['height']=max(60,min(250,int(value)))
            else:raise ValueError('TEXTBOX BG / BORDER / TEXT / OPACITY / HEIGHT')
            message='Text box '+prop+' updated'
        elif action=='LINES':
            # Enter a complete conversation in one Code Chat command.
            # Separate speakers' lines with |. Existing lines are preserved.
            parts=[part.strip() for part in cmd[6:].split('|') if part.strip()]
            if not parts:raise ValueError('Use LINES Alice: Hello! | Bob: Hi!')
            was_empty=not dialogue_lines
            dialogue_lines.extend(parts)
            if was_empty:
                dialogue_index=0;dialogue=dialogue_lines[0]
                dialogue_started_ms=pygame.time.get_ticks()
            message=str(len(parts))+' consecutive dialogue lines added.'
        elif action=='BGSIZE':
            if len(words)!=3:raise ValueError('Use BGSIZE 960 640')
            width,height=int(words[1]),int(words[2])
            if not 20<=width<=20000 or not 20<=height<=20000:
                raise ValueError('Background width and height must be 20 to 20000')
            bg_rect[2:4]=[width,height]
            message='Background resized to '+str(width)+' x '+str(height)
        elif action=='SAVETEXT':
            # Save dialogue as UTF-8 text next to the project; does not execute user input.
            import re
            safe_name=re.sub(r'[^a-zA-Z0-9_-]+','_',current_scene)[:60] or 'scene'
            output=os.path.join(os.path.dirname(manual_path),safe_name+'_dialogue.txt')
            with open(output,'w',encoding='utf-8') as file:
                file.write('\n'.join(dialogue_lines or ([scene_dialogue] if scene_dialogue else [])))
            message='Dialogue text saved: '+output
        elif action=='LINE':
            content=cmd[5:].strip()
            if not content:raise ValueError('Use LINE Character 1: Hello!')
            dialogue_lines.append(content)
            if len(dialogue_lines)==1:
                dialogue_index=0;dialogue=content;dialogue_started_ms=pygame.time.get_ticks()
            message='Dialogue line '+str(len(dialogue_lines))+' added.'
        elif action=='NEXT':
            advance_dialogue()
        elif action=='CLEARLINES':
            dialogue_lines.clear();dialogue_index=0;dialogue='';message='Dialogue timeline cleared.'
        elif action=='COLLISION':
            if len(words)!=2 or words[1].upper() not in ('ON','OFF'):raise ValueError('Use COLLISION ON or COLLISION OFF')
            collision_enabled=words[1].upper()=='ON';message='Collision '+words[1].upper()
        elif action=='SCENE':
            if len(words)<2:raise ValueError('Use SCENE Scene 2')
            change_scene(' '.join(words[1:]))
        elif action=='MOVEMENT':
            global movement_enabled
            if len(words)!=2 or words[1].upper() not in ('ON','OFF'):raise ValueError('Use MOVEMENT ON or MOVEMENT OFF')
            movement_enabled=words[1].upper()=='ON';message='Movement '+words[1].upper()
        elif action=='CHOICE':
            # CHOICE Help her -> Scene 2
            if '->' not in cmd:raise ValueError('Use CHOICE Help her -> Scene 2')
            label,target_name=cmd[7:].split('->',1)
            if not label.strip() or not target_name.strip():raise ValueError('Choice label and target are required')
            scene_choices.append({'label':label.strip(),'target':target_name.strip()})
            message='Choice added: '+label.strip()+' -> '+target_name.strip()
        elif action=='TYPEWRITER':
            # TYPEWRITER 35 / TYPEWRITER OFF
            if len(words)!=2:raise ValueError('Use TYPEWRITER 35 or TYPEWRITER OFF')
            typewriter_cps=0 if words[1].upper()=='OFF' else max(1,min(200,int(words[1])))
            dialogue_started_ms=pygame.time.get_ticks()
            message='Typewriter '+('off' if not typewriter_cps else str(typewriter_cps)+' letters/sec')
        elif action=='TRANSITION':
            # Select any image, then TRANSITION FADEIN 0.8 (or FADEOUT/SLIDELEFT/SLIDERIGHT)
            target=get_selected()
            if not target or not target.get('path'):
                raise ValueError('Select an imported image or image button first')
            if len(words)!=3:raise ValueError('Use TRANSITION FADEIN 0.8')
            effect=words[1].upper()
            if effect not in ('FADEIN','FADEOUT','SLIDELEFT','SLIDERIGHT'):
                raise ValueError('Effects: FADEIN FADEOUT SLIDELEFT SLIDERIGHT')
            seconds=max(.1,min(10.,float(words[2])))
            target['_transition']={'effect':effect,'start':pygame.time.get_ticks(),'duration':int(seconds*1000)}
            target['visible']=True
            message=target['name']+' transition: '+effect
        elif action in ('TELEPORT','POSITION'):
            if len(words)!=3:raise ValueError('TELEPORT 200 150')
            target=get_selected()
            if not target:raise ValueError('Select an item first')
            target['x']=float(words[1]);target['y']=float(words[2]);message='Position updated'
        elif action in ('ADD','SUBTRACT'):
            if len(words)!=4 or words[2].upper()!='TO':raise ValueError('ADD 10 TO coins / SUBTRACT 5 TO coins')
            amount=float(words[1]) * (1 if action=='ADD' else -1)
            key=words[3];value=float(story_flags.get(key,0))+amount
            story_flags[key]=int(value) if value.is_integer() else value
            message=key+' = '+str(story_flags[key])
        elif action=='HELP':
            message='ANIM NEW Walk | ANIM FPS 12 | PLAY ANIMATION Walk | ON CLICK TOGGLE ANIMATION Walk | TEXTBOX BG #443344 | MOVE [Character N] RIGHT 50 | SAY [Character N] Hello | SET flag value | IF flag value SAY text'
        elif action=='MOVE':
            # MOVE RIGHT 50 OR MOVE Character 1 RIGHT 50
            if len(words)==3:
                target=get_selected();direction=words[1].upper();amount=int(words[2])
            elif len(words)==5 and words[1].lower()=='character':
                target=next((i for i in items if i['name'].lower()==('character '+words[2]).lower()),None)
                direction=words[3].upper();amount=int(words[4])
            else:raise ValueError('Use MOVE RIGHT 50 or MOVE Character 1 RIGHT 50')
            if not target:raise ValueError('Select or import a character first')
            if target['kind']!='character':raise ValueError('Movement requires a character')
            directions={'RIGHT':(1,0),'LEFT':(-1,0),'UP':(0,-1),'DOWN':(0,1)}
            if direction not in directions:raise ValueError('Direction must be UP, DOWN, LEFT, or RIGHT')
            dx,dy=directions[direction];target['x']+=dx*amount;target['y']+=dy*amount
            message=target['name']+' moved '+direction+' '+str(amount)+' pixels.'
        elif action=='SAY':
            rest=cmd[4:].strip()
            if not rest:raise ValueError('Use SAY Hello! or SAY Character 1 Hello!')
            if len(words)>=4 and words[1].lower()=='character' and words[2].isdigit():
                name='Character '+words[2]
                target=next((i for i in items if i['name'].lower()==name.lower()),None)
                if not target:raise ValueError(name+' not found')
                selected_id=target['id']
                rest=' '.join(words[3:])
            set_scene_dialogue(rest);message='Dialogue: '+rest
        elif action=='SET':
            if len(words)!=3:raise ValueError('Use SET flag value')
            story_flags[words[1]]=words[2];message='Story variable '+words[1]+' = '+words[2]
        elif action=='IF':
            if len(words)<5 or words[3].upper()!='SAY':raise ValueError('Use IF flag value SAY message')
            if story_flags.get(words[1])==words[2]:
                set_scene_dialogue(' '.join(words[4:]));message='Branch matched: '+dialogue
            else:message='Branch skipped: condition is false'
        elif action=='HIDE' or action=='SHOW':
            target=get_selected()
            if not target:raise ValueError('Select an item first')
            target['visible']=(action=='SHOW');message=target['name']+' '+('shown' if target['visible'] else 'hidden')
        elif action=='PLAY':
            animation_play=True;message='Animation playback enabled'
        elif action=='STOP':
            animation_play=False;message='Animation playback stopped'
        elif action=='CLEAR':
            set_scene_dialogue('');message='Dialogue cleared'
        else:raise ValueError('Unknown command. Type HELP for examples.')
    except (ValueError,IndexError) as e:message='Command error: '+str(e)
    chat_history.append(message)
    if len(chat_history)>100:del chat_history[:-100]


# V21 safe, data-only event commands. Never eval/exec scripts from projects.
import random as _rpg_random

def _rpg_lookup(objects, name, owner):
    if name.upper() in ('SELF','THIS'):return owner
    return next((o for o in objects if o.get('name','').casefold()==name.casefold()),None)

def _rpg_compare(left, operator, right):
    try:
        left,right=float(left),float(right)
    except (ValueError,TypeError):
        left,right=str(left),str(right)
    return {'==':lambda:a_eq(left,right),'!=':lambda:not a_eq(left,right),
            '>':lambda:left>right,'<':lambda:left<right,
            '>=':lambda:left>=right,'<=':lambda:left<=right}[operator]()

def a_eq(a,b):return a==b

# Version 31: additional data-only RPG commands (no eval or arbitrary code execution).
def rpg_v31_action(owner, args, objects, say, scene_change, flags, depth):
    """Return True if an action was recognized and processed."""
    if not args:return False
    op=args[0].upper()
    flags=flags if flags is not None else {}
    def target(name):
        return _rpg_lookup(objects,name,owner)
    def number(value):
        return float(value)
    def fmt(value):
        return int(value) if float(value).is_integer() else value
    if op in ('ADD','REMOVE') and len(args)>=3 and args[1].upper()=='ITEM':
        item=' '.join(args[2:]);inv=flags.setdefault('inventory',{})
        if not isinstance(inv,dict):inv={};flags['inventory']=inv
        qty=int(inv.get(item,0))+(1 if op=='ADD' else -1)
        if qty>0:inv[item]=qty
        else:inv.pop(item,None)
        return True
    if op=='CLEAR' and len(args)>1 and args[1].upper()=='INVENTORY':
        flags['inventory']={};return True
    if op=='HAS' and len(args)>=4 and args[1].upper()=='ITEM':
        # HAS ITEM Key SAY You have it!
        split=next((j for j in range(2,len(args)) if args[j].upper() in ('SAY','SCENE','GOTO','SHOW','HIDE')),None)
        if split:
            item=' '.join(args[2:split]);inv=flags.get('inventory',{})
            if isinstance(inv,dict) and inv.get(item,0)>0:
                rpg_run(owner,'_item',objects,say,scene_change,flags,depth+1,lines_override=[' '.join(args[split:])])
        return True
    if op in ('START','COMPLETE','FAIL','RESET') and len(args)>=3 and args[1].upper()=='QUEST':
        quest=' '.join(args[2:]);quests=flags.setdefault('quests',{})
        if not isinstance(quests,dict):quests={};flags['quests']=quests
        if op=='RESET':quests.pop(quest,None)
        else:quests[quest]={'START':'active','COMPLETE':'completed','FAIL':'failed'}[op]
        return True
    if op=='SHOW' and len(args)>2 and args[1].upper()=='QUEST' and args[2].upper()=='LOG':
        quests=flags.get('quests',{});say('Quests: '+(', '.join(f'{k} ({v})' for k,v in quests.items()) if isinstance(quests,dict) and quests else 'None'));return True
    if op in ('ADD','REMOVE') and len(args)>=3 and args[1].upper() in ('GOLD','XP'):
        name=args[1].lower();delta=number(args[2])*(1 if op=='ADD' else -1)
        flags[name]=max(0,fmt(float(flags.get(name,0))+delta));return True
    if op=='LEVEL' and len(args)>1 and args[1].upper()=='UP':
        flags['level']=int(flags.get('level',1))+1;return True
    if op=='SET' and len(args)>=3 and args[1].upper() in ('LEVEL','GOLD','XP'):
        flags[args[1].lower()]=max(0,fmt(number(args[2])));return True
    if op=='MULTIPLY' and len(args)>=4 and args[2].upper()=='BY':
        name=args[1];flags[name]=fmt(float(flags.get(name,0))*number(args[3]));return True
    if op=='DIVIDE' and len(args)>=4 and args[2].upper()=='BY':
        name=args[1];div=number(args[3]);
        if div:flags[name]=fmt(float(flags.get(name,0))/div)
        return True
    if op=='RANDOM' and len(args)>=6 and args[1].upper()=='SET' and args[3].upper()=='FROM' and args[5].upper()=='TO' and len(args)>=7:
        flags[args[2]]=_rpg_random.randint(int(args[4]),int(args[6]));return True
    if op=='MOVE' and len(args)>=4 and args[1].upper()=='TO':
        owner['x'],owner['y']=number(args[2]),number(args[3]);return True
    if op=='MOVE' and len(args)>=3 and args[1].upper() in ('TOWARD','AWAY'):
        other=target(' '.join(args[3:] if args[1].upper()=='AWAY' and args[2].upper()=='FROM' else args[2:]))
        if other and other is not owner:
            import math
            x,y=item_world_position(owner) if owner in objects else (owner.get('x',0),owner.get('y',0))
            tx,ty=item_world_position(other) if other in objects else (other.get('x',0),other.get('y',0))
            dx,dy=tx-x,ty-y;dist=math.hypot(dx,dy)
            if dist:
                sign=-1 if args[1].upper()=='AWAY' else 1
                owner['x']=owner.get('x',0)+sign*min(30,dist)*dx/dist
                owner['y']=owner.get('y',0)+sign*min(30,dist)*dy/dist
        return True
    if op in ('SHOW','HIDE') and len(args)>1 and args[1].upper() not in ('QUEST',):
        other=target(' '.join(args[1:]));
        if other:other['visible']=(op=='SHOW')
        return True
    if op=='TELEPORT' and len(args)>=4 and args[1].upper()=='TO':
        owner['x'],owner['y']=number(args[2]),number(args[3]);return True
    if op=='SET' and len(args)>=3 and args[1].upper()=='SPEED':
        owner['speed']=max(0,min(100,number(args[2])));return True
    if op=='SET' and len(args)>=3 and args[1].upper() in ('ZINDEX','LAYER'):
        owner['zindex']=int(args[2]);return True
    if op=='BRING' and len(args)>=3 and args[1].upper()=='TO' and args[2].upper()=='FRONT':
        owner['zindex']=max([o.get('zindex',0) for o in objects]+[0])+1;return True
    if op=='SEND' and len(args)>=3 and args[1].upper()=='TO' and args[2].upper()=='BACK':
        owner['zindex']=min([o.get('zindex',0) for o in objects]+[0])-1;return True
    if op=='PARENT' and len(args)>=4 and 'TO' in [x.upper() for x in args]:
        ix=[x.upper() for x in args].index('TO');child=target(' '.join(args[1:ix]));parent=target(' '.join(args[ix+1:]))
        if child and parent and child is not parent:attach_item(child,parent)
        return True
    if op=='UNPARENT' and len(args)>1:
        child=target(' '.join(args[1:]));
        if child:detach_item(child)
        return True
    if op=='NEXT' and len(args)>1 and args[1].upper()=='SCENE' and scene_change:
        if current_scene in scene_order and scene_order.index(current_scene)<len(scene_order)-1:scene_change(scene_order[scene_order.index(current_scene)+1]);return True
        return True
    if op=='PREVIOUS' and len(args)>1 and args[1].upper()=='SCENE' and scene_change:
        if current_scene in scene_order and scene_order.index(current_scene)>0:scene_change(scene_order[scene_order.index(current_scene)-1]);return True
        return True
    if op=='RESTART' and len(args)>1 and args[1].upper()=='SCENE' and scene_change:
        scene_change(current_scene);return True
    if op=='SAY' and len(args)>=2 and args[1].upper()=='STATS':
        say('Gold: '+str(flags.get('gold',0))+' | XP: '+str(flags.get('xp',0))+' | Level: '+str(flags.get('level',1)));return True
    return False

_v31_every_next={}

# V64: click-to-advance, randomized NPC conversations.
_npc_dialogue_queue = {}

def npc_dialogue_groups(source):
    """Read RANDOM DIALOGUE / CHOICE / SAY / END RANDOM DIALOGUE blocks."""
    groups=[];current=None;inside=False
    for raw in source:
        line=str(raw).strip()
        upper=line.upper()
        if upper=='RANDOM DIALOGUE':inside=True;continue
        if upper=='END RANDOM DIALOGUE':
            if current:groups.append(current)
            inside=False;current=None;continue
        if not inside:continue
        if upper=='CHOICE':
            if current:groups.append(current)
            current=[]
        elif upper.startswith('SAY '):
            if current is None:current=[]
            current.append(line[4:].strip())
    return groups

def npc_dialogue_step(owner, event, lines, say, scene_key):
    if event.lower()!='click':return False
    groups=npc_dialogue_groups(lines)
    if not groups:return False
    key=(scene_key,owner.get('id',id(owner)))
    queue=_npc_dialogue_queue.get(key)
    if not queue:
        queue=list(_rpg_random.choice(groups))
    say(queue.pop(0))
    if queue:_npc_dialogue_queue[key]=queue
    else:_npc_dialogue_queue.pop(key,None)
    return True

def rpg_run(owner, event, objects, say, scene_change=None, flags=None, depth=0, lines_override=None):
    if depth>8:return
    scripts=owner.get('events',{})
    lines=lines_override if lines_override is not None else scripts.get(event.lower(),[])
    if isinstance(lines,str):lines=lines.splitlines()
    if npc_dialogue_step(owner,event,lines,say,current_scene):return
    for line in list(lines)[:80]:
        line=str(line).strip()
        if not line or line.startswith('#'):continue
        try:
            import shlex
            args=shlex.split(line)
            if not args:continue
            op=args[0].upper()
            if rpg_v31_action(owner,args,objects,say,scene_change,flags,depth):continue
            if op=='EVERY' and len(args)>=4 and args[2].upper() in ('SECONDS','SECOND'):
                seconds=float(args[1])
                if seconds<=0 or seconds>3600:continue
                key=(current_scene,owner.get('id','scene'),event,line)
                now=pygame.time.get_ticks()
                if now>=_v31_every_next.get(key,0):
                    _v31_every_next[key]=now+int(seconds*1000)
                    rpg_run(owner,'_every',objects,say,scene_change,flags,depth+1,lines_override=[' '.join(args[3:])])
            elif op=='AFTER' and len(args)>=3:
                seconds=float(args[1])
                if not 0<=seconds<=3600:continue
                schedule_rpg_action(owner,seconds,' '.join(args[2:]),objects,say,scene_change,flags)
            elif op=='CHANCE':
                chance=max(0,min(100,float(args[1])))
                if _rpg_random.random()*100<chance:
                    rpg_run({'events':{'trigger':[' '.join(args[2:])]}},'trigger',objects,say,scene_change,flags,depth+1)
            elif op in ('ADD','SUBTRACT') and len(args)>=4 and args[2].upper()=='TO' and flags is not None:
                name=args[3];amount=float(args[1])*(1 if op=='ADD' else -1)
                value=float(flags.get(name,0))+amount
                flags[name]=int(value) if value.is_integer() else value
            elif op=='IF' and len(args)>=5 and args[2] in ('==','!=','>','<','>=','<=') and flags is not None:
                if _rpg_compare(flags.get(args[1],0),args[2],args[3]):
                    rpg_run(owner,'_conditional',objects,say,scene_change,flags,depth+1,lines_override=[' '.join(args[4:])])
            elif op=='RANDOM' and len(args)>=4 and args[1].upper()=='CHANCE':
                probability=float(args[2].rstrip('%'))
                if _rpg_random.random()*100<max(0,min(100,probability)):
                    rpg_run(owner,'_random',objects,say,scene_change,flags,depth+1,lines_override=[' '.join(args[3:])])
            elif op=='SOUND' and len(args)>=3 and args[1].upper() in ('PLAY','LOOP'):
                play_sound(' '.join(args[2:]),args[1].upper()=='LOOP')
            elif op=='SOUND' and len(args)>=2 and args[1].upper()=='STOP':
                stop_sound(' '.join(args[2:]).strip() or None)
            elif op=='TELEPORT' and len(args)>=3:
                owner['x']=float(args[1]);owner['y']=float(args[2])
            elif op=='DEAL' and len(args)>=5 and args[1].upper()=='DAMAGE' and args[3].upper()=='TO':
                target=_rpg_lookup(objects,' '.join(args[4:]),owner)
                if target:
                    before=max(0,int(target.get('health',100)))
                    target['health']=max(0,before-max(0,int(args[2])))
                    if before>0 and target['health']==0:
                        rpg_run(target,'death',objects,say,scene_change,flags,depth+1)
            elif op=='HEAL' and len(args)>=4 and args[-2].upper()=='BY':
                target=_rpg_lookup(objects,' '.join(args[1:-2]),owner)
                if target:target['health']=max(0,int(target.get('health',100))+max(0,int(args[-1])))
            elif op=='SET' and len(args)>=3 and args[1].upper()=='OPACITY':
                owner['opacity']=max(0,min(255,int(float(args[2])*255 if float(args[2])<=1 else float(args[2]))))
            elif op=='SAY':say(' '.join(args[1:]))
            elif op in ('PLAY','TOGGLE','STOP') and len(args)>=2 and args[1].upper()=='ANIMATION':
                if op=='STOP':owner['_anim_playing']=False
                elif len(args)>=3:play_named_animation(owner,' '.join(args[2:]),op)
            elif op in ('HIDE','DISAPPEAR','KILL'):owner['visible']=False
            elif op=='SHOW':owner['visible']=True
            elif op=='MOVE' and len(args)>=3:
                directions={'RIGHT':(1,0),'LEFT':(-1,0),'UP':(0,-1),'DOWN':(0,1)}
                dx,dy=directions[args[1].upper()];distance=max(-2000,min(2000,int(args[2])))
                owner['x']=owner.get('x',0)+dx*distance;owner['y']=owner.get('y',0)+dy*distance
            elif op=='HEALTH' and len(args)>1:owner['health']=max(0,int(args[1]))
            elif op=='DAMAGE' and len(args)>1:
                owner['health']=max(0,int(owner.get('health',100))-max(0,int(args[1])))
                if owner['health']==0:rpg_run(owner,'death',objects,say,scene_change,flags,depth+1)
            elif op=='HEAL' and len(args)>1:owner['health']=int(owner.get('health',100))+max(0,int(args[1]))
            elif op=='SET' and len(args)>2 and flags is not None:flags[args[1]]=' '.join(args[2:])
            elif op=='IF' and len(args)>=4 and args[2] not in ('==','!=','>','<','>=','<=') and flags is not None and str(flags.get(args[1]))==args[2]:
                rpg_run({'events':{'trigger':[' '.join(args[3:])]}},'trigger',objects,say,scene_change,flags,depth+1)
            elif op in ('SCENE','GOTO','CHANGE') and scene_change:
                destination=' '.join(args[2:] if op=='CHANGE' and len(args)>2 and args[1].upper()=='SCENE' else args[1:])
                scene_change(destination)
            elif op=='SPAWN' and len(args)>1:
                template=next((x for x in objects if x.get('name','').lower()==args[1].lower()),None)
                if template:template['visible']=True
        except (ValueError,IndexError,KeyError,TypeError):continue

# Delayed event actions use monotonic pygame ticks, not sleep(), so animations remain responsive.
_rpg_scheduled=[]
def schedule_rpg_action(owner, seconds, command, objects, say, scene_change, flags):
    if len(_rpg_scheduled)>=256:return
    scene_key=globals().get('current_scene') if globals().get('app_page')=='editor' else globals().get('scene_name')
    _rpg_scheduled.append((pygame.time.get_ticks()+int(seconds*1000),scene_key,owner,command,objects,say,scene_change,flags))

def process_rpg_schedule():
    now=pygame.time.get_ticks()
    scene_key=globals().get('current_scene') if globals().get('app_page')=='editor' else globals().get('scene_name')
    pending=list(_rpg_scheduled)
    _rpg_scheduled.clear()
    for due,origin,owner,command,objects,say,scene_change,flags in pending:
        if origin!=scene_key:continue
        if due>now:
            _rpg_scheduled.append((due,origin,owner,command,objects,say,scene_change,flags))
        else:
            rpg_run(owner,'_delayed',objects,say,scene_change,flags,lines_override=[command])

_rpg_editor_last_second=-1
_v58_touch_pairs=set()
_v58_touch_scene=None

def v58_touch_events():
    """Fire contact/enter/exit for overlapping visible entities during preview."""
    global _v58_touch_pairs,_v58_touch_scene
    if _v58_touch_scene!=current_scene:
        _v58_touch_pairs=set();_v58_touch_scene=current_scene
    visible=[obj for obj in items if obj.get('visible',True)]
    current=set()
    def names(obj):
        return {str(obj.get('name','')).lower(),str(obj.get('id','')).lower(),str(obj.get('kind','')).lower(), 'player' if obj.get('id')==player_id else ''}
    for index,a in enumerate(visible):
        ax,ay=item_world_position(a)
        ar=pygame.Rect(round(ax),round(ay),max(1,round(a['w'])),max(1,round(a['h'])))
        for b in visible[index+1:]:
            bx,by=item_world_position(b)
            br=pygame.Rect(round(bx),round(by),max(1,round(b['w'])),max(1,round(b['h'])))
            if not ar.colliderect(br):continue
            pair=(min(a['id'],b['id']),max(a['id'],b['id']))
            current.add(pair)
            for owner,other in ((a,b),(b,a)):
                for n in names(other):
                    if n:
                        editor_event(owner,'touch:'+n)
                        if pair not in _v58_touch_pairs:editor_event(owner,'enter:'+n)
    for pair in _v58_touch_pairs-current:
        a=next((obj for obj in items if obj['id']==pair[0]),None)
        b=next((obj for obj in items if obj['id']==pair[1]),None)
        if a and b:
            for owner,other in ((a,b),(b,a)):
                for n in names(other):
                    if n:editor_event(owner,'exit:'+n)
    _v58_touch_pairs=current

def editor_event(owner,event):
    global dialogue
    def speak(value):
        global dialogue
        set_scene_dialogue(value)
    rpg_run(owner,event,items,speak,change_scene,story_flags)

def editor_start_events():
    editor_event({'events':scene_events},'start')
    for obj in list(items):editor_event(obj,'start')

DESKTOP_BUILDER_SOURCE = '"""Build the exported 2d appmaker game for the CURRENT operating system.\nRequires: python -m pip install pygame-ce pyinstaller\nRun: python build_desktop.py\n"""\nimport json\nimport subprocess\nimport sys\nfrom pathlib import Path\n\nroot=Path(__file__).resolve().parent\nconfig=json.loads((root/\'game.json\').read_text(encoding=\'utf-8\'))\nname=str(config.get(\'project_name\',\'MyGame\'))\nname=\'\'.join(c if c.isalnum() or c in \' _-\' else \'_\' for c in name).strip()[:60] or \'MyGame\'\nsep=\';\' if sys.platform.startswith(\'win\') else \':\'\nargs=[sys.executable,\'-m\',\'PyInstaller\',\'--noconfirm\',\'--clean\',\'--windowed\',\n      \'--name\',name,\'--distpath\',str(root/\'dist\'),\'--workpath\',str(root/\'build\'),\n      \'--specpath\',str(root/\'build\'),\n      \'--add-data\',str(root/\'game.json\')+sep+\'.\']\nif (root/\'assets\').is_dir():args+=[\'--add-data\',str(root/\'assets\')+sep+\'assets\']\nargs.append(str(root/\'play_game.py\'))\nprint(\'Building standalone game for\',sys.platform)\nsubprocess.run(args,check=True)\nprint(\'Build finished. Distribute the complete dist/\'+name+\' folder (or .app on macOS).\')\n'

def export_game():
    """Export a portable source ZIP with all assets and a playable Pygame runtime."""
    global message
    if not project_file:
        message='Open a project before exporting.';return None
    if not save_current_project():return None
    try:
        player_source=bundled_player_path()
        if not player_source.is_file():
            message='Missing bundled game player.';return None
        data=json.loads(Path(project_file).read_text(encoding='utf-8'))
        assets={};missing=[]
        def copy_asset(value):
            if not value:return None
            source=Path(value).expanduser()
            if not source.is_file():
                missing.append(str(value));return None
            resolved=str(source.resolve())
            if resolved not in assets:
                assets[resolved]='assets/'+str(len(assets)+1).zfill(4)+source.suffix.lower()
            return assets[resolved]
        for scene in data.get('scenes',{}).values():
            scene['background']=copy_asset(scene.get('background'))
            if scene.get('dialogue_style',{}).get('image'):
                scene['dialogue_style']['image']=copy_asset(scene['dialogue_style']['image'])
            scene['sounds']={name:copy_asset(path) for name,path in scene.get('sounds',{}).items()}
            for item in scene.get('items',[]):
                item['path']=copy_asset(item.get('path'))
                if item.get('frames'):
                    item['frames']=[copy_asset(f) for f in item['frames']]
                for clip in item.get('animations',{}).values():
                    clip['frames']=[copy_asset(f) for f in clip.get('frames',[])]
        if missing:
            message='Export canceled: missing '+str(len(missing))+' asset(s). Restore original assets.';return None
        destination=Path(project_file).with_name(Path(project_file).stem+'_playable.zip')
        with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr('game.json',json.dumps(data,ensure_ascii=False,indent=2))
            z.write(player_source,'play_game.py')
            z.writestr('README.txt','2d appmaker portable game export\n\nInstall Python 3 and pygame-ce: python3 -m pip install pygame-ce\nUnzip, then run: python3 play_game.py\n\nTo build a standalone Windows/macOS/Linux game, run build_desktop.py on that operating system.\nThe builder uses PyInstaller and creates a dist/ folder for distribution.\nNo Steam partnership or 2d appmaker installation is required for players of a built game.\n')
            z.writestr('build_desktop.py',DESKTOP_BUILDER_SOURCE)
            z.writestr('requirements-build.txt','pygame-ce>=2.5\npyinstaller>=6\n')
            for original,relative in assets.items():z.write(original,relative)
        message='Exported portable game: '+str(destination)
        print('2d appmaker export saved to:',destination)
        return destination
    except Exception as e:
        message='Export failed: '+str(e)
        traceback.print_exc()
        return None

def build_desktop_game():
    global message
    if sys.platform == 'emscripten':
        message = 'Desktop builds are available only in the Mac/Windows/Linux app.'
        return
    """Build a standalone desktop game for the current OS using PyInstaller."""
    archive=export_game()
    if archive is None:return
    try:
        import importlib.util
        if importlib.util.find_spec('PyInstaller') is None:
            message='Playable ZIP exported. Install PyInstaller (python3 -m pip install pyinstaller), then click Build Desktop Game again.'
            return
        import threading
        def build_worker():
            global message
            try:
                with tempfile.TemporaryDirectory(prefix='appmaker_build_') as temp:
                    with zipfile.ZipFile(archive) as z:z.extractall(temp)
                    result=subprocess.run([sys.executable,str(Path(temp)/'build_desktop.py')],cwd=temp,capture_output=True,text=True)
                    if result.returncode:
                        message='Build failed: '+(result.stderr or result.stdout)[-210:].replace('\n',' ')
                        print('Build failed:',result.stdout,result.stderr)
                        return
                    output=Path(temp)/'dist'
                    target=Path(project_file).with_name(Path(project_file).stem+'_desktop_build')
                    if target.exists():shutil.rmtree(target)
                    shutil.copytree(output,target)
                    message='Desktop game built: '+str(target)
                    print('Standalone desktop build:',target)
            except Exception as exc:
                message='Desktop build failed: '+str(exc)
                traceback.print_exc()
        threading.Thread(target=build_worker,daemon=True).start()
        message='Building standalone game in background; this may take a few minutes...'
    except Exception as e:
        message='Could not start desktop build: '+str(e)


def toolbar_buttons():
    labels=['Import','Files','Edit','Game Mode','Animation','Entities']
    x=16;out=[]
    for label in labels:
        w=104 if label in ('Game Mode','Animation','Entities') else 83
        out.append((label,pygame.Rect(x,47,w,34)));x+=w+7
    return out

def remove_selected_with_children():
    obj=get_selected()
    if obj is None:return
    for child in list(items):
        if child.get('parent_id')==obj['id']:detach_item(child)
    items.remove(obj)

def entity_drawer_rect():
    # Floating, draggable panel. Keep its entire header and controls on screen.
    global entity_drawer_pos
    canvas=canvas_rect()
    w=min(315,max(210,canvas.w//3))
    h=min(620,max(380,screen.get_height()-112))
    if entity_drawer_pos is None:
        entity_drawer_pos=[canvas.x+9,canvas.y+30]
    x=max(4,min(int(entity_drawer_pos[0]),screen.get_width()-w-4))
    y=max(43,min(int(entity_drawer_pos[1]),screen.get_height()-h-4))
    entity_drawer_pos=[x,y]
    return pygame.Rect(x,y,w,h)

def entity_drawer_header_rect():
    box=entity_drawer_rect()
    return pygame.Rect(box.x,box.y,box.w,52)

def entity_visible_capacity():
    return max(1,(entity_drawer_rect().h-265)//36)

def entity_folder_entries():
    """A scene-local folder tree. Entries are folders, images, or imported audio."""
    entries=[]
    for folder in ['']+list(entity_folders):
        if folder:
            entries.append(('folder',folder))
            if folder in entity_folder_closed:continue
        for obj in items:
            if entity_assignments.get(str(obj['id']), '')==folder:
                entries.append(('entity',obj))
        for name in scene_sounds:
            if audio_assignments.get(name,'')==folder:
                entries.append(('sound',name))
    return entries

def entity_sound_rows():
    box=entity_drawer_rect()
    entries=entity_folder_entries()
    limit=max(0,len(entries)-entity_visible_capacity())
    offset=max(0,min(entity_scroll,limit))
    return [(kind,value,pygame.Rect(box.x+12,box.y+92+j*36,box.w-40,32))
            for j,(kind,value) in enumerate(entries[offset:offset+entity_visible_capacity()])]

def entity_drawer_scrollbar():
    box=entity_drawer_rect();top=box.y+92;height=entity_visible_capacity()*36
    total=len(entity_folder_entries());capacity=entity_visible_capacity()
    if total<=capacity:return None,None
    track=pygame.Rect(box.right-21,top,7,height)
    thumb_h=max(24,int(height*capacity/total))
    maximum=total-capacity
    thumb_y=top+int((height-thumb_h)*min(entity_scroll,maximum)/maximum)
    return track,pygame.Rect(track.x,thumb_y,track.w,thumb_h)

def entity_move_menu_rect():
    box=entity_drawer_rect()
    return pygame.Rect(box.x+145,box.y+92,box.w-157,32*(1+len(entity_folders)))

def entity_drawer_controls():
    box=entity_drawer_rect();y=box.bottom-98
    return {'rename':pygame.Rect(box.x+12,y,box.w//2-17,28),
            'edit':pygame.Rect(box.x+box.w//2+4,y,box.w//2-16,28),
            'parent':pygame.Rect(box.x+12,y+33,box.w-24,28),
            'visible':pygame.Rect(box.x+12,y+66,box.w//2-17,27),
            'detach':pygame.Rect(box.x+box.w//2+4,y+66,box.w//2-16,27),
            'audio':pygame.Rect(box.x+12,y-38,box.w-24,29)}

def draw_drawer_fitted(label, x, y, max_width, color):
    """Render drawer text inside its panel, with an ellipsis when necessary."""
    text_value=editor_ui(label)
    font=interface_font(SMALL,text_value)
    text_value=display_safe(text_value)
    if max_width <= 0:
        return
    if font.size(text_value)[0] > max_width:
        ellipsis='…'
        while text_value and font.size(text_value+ellipsis)[0] > max_width:
            text_value=text_value[:-1]
        text_value=(text_value+ellipsis) if text_value else ellipsis
    screen.blit(font.render(text_value,True,color),(int(x),int(y)))

def draw_entity_drawer():
    box=entity_drawer_rect()
    pygame.draw.rect(screen,COL['panel'],box,border_radius=9)
    pygame.draw.rect(screen,COL['accent'],box,2,border_radius=9)
    # Both header lines must respect the floating panel width in every language.
    draw_drawer_fitted(editor_ui('SCENE ENTITIES')+'  '+editor_ui('[DRAG HERE]'),box.x+13,box.y+12,box.w-26,COL['accent'])
    counts=f'{len(items)} {editor_ui("entities count")} · {len(scene_sounds)} {editor_ui("audio count")} · {len(entity_folders)} {editor_ui("folders count")}'
    draw_drawer_fitted(counts,box.x+13,box.y+34,box.w-26,COL['muted'])
    button(pygame.Rect(box.x+12,box.y+57,126,29),editor_ui('+ New Folder'),small=True)
    button(pygame.Rect(box.x+145,box.y+57,box.w-157,29),editor_ui('Move to Folder'),small=True)
    clip=screen.get_clip();screen.set_clip(pygame.Rect(box.x+8,box.y+90,box.w-16,entity_visible_capacity()*36+2))
    for kind,value,r in entity_sound_rows():
        if kind=='folder':
            pygame.draw.rect(screen,COL['selected'],r,border_radius=5)
            symbol='+' if value in entity_folder_closed else '-'
            draw_text('['+symbol+']  '+value[:22],r.x+8,r.y+8,COL['text'],SMALL)
            continue
        selected=(value==selected_drawer_sound) if kind=='sound' else (value['id']==selected_id and selected_drawer_sound is None)
        pygame.draw.rect(screen,COL['selected'] if selected else COL['canvas'],r,border_radius=5)
        if kind=='sound':
            label='♫ '+value
            draw_text(label[:24],r.x+8,r.y+8,COL['text'],SMALL)
            button(pygame.Rect(r.right-34,r.y+2,30,28),'▶',small=True)
        else:
            obj=value
            label=('▸ ' if obj.get('parent_id') else '')+obj['name']+' ('+obj['kind']+')'
            if not obj.get('visible',True):label+=' [hidden]'
            draw_text(label[:24],r.x+8,r.y+8,COL['text'],SMALL)
            button(pygame.Rect(r.right-34,r.y+2,30,28),'+',small=True)
    screen.set_clip(clip)
    if entity_move_menu:
        menu=entity_move_menu_rect()
        pygame.draw.rect(screen,COL['panel'],menu,border_radius=5)
        pygame.draw.rect(screen,COL['accent'],menu,2,border_radius=5)
        for index,folder in enumerate(['']+list(entity_folders)):
            draw_text((folder or editor_ui('Ungrouped'))[:24],menu.x+9,menu.y+index*32+7,COL['text'],SMALL)
    # Highlight a folder as the drop destination and show a small drag label.
    if entity_row_drag and entity_row_drag.get('active'):
        mouse=pygame.mouse.get_pos()
        for kind,value,r in entity_sound_rows():
            if kind=='folder' and r.collidepoint(mouse):
                pygame.draw.rect(screen,COL['accent'],r,3,border_radius=5)
        label=next((o['name'] for o in items if o['id']==entity_row_drag['value']),str(entity_row_drag['value'])) if entity_row_drag['kind']=='entity' else entity_row_drag['value']
        tip=pygame.Rect(mouse[0]+12,mouse[1]+8, min(260,max(100,len(label)*8+18)),27)
        pygame.draw.rect(screen,COL['selected'],tip,border_radius=5)
        draw_text(label[:27],tip.x+7,tip.y+5,COL['text'],SMALL)
    track,thumb=entity_drawer_scrollbar()
    if track:
        pygame.draw.rect(screen,COL['bg'],track,border_radius=4)
        pygame.draw.rect(screen,COL['accent'],thumb,border_radius=4)
    obj=get_selected() if selected_drawer_sound is None else None
    if selected_drawer_sound is not None:
        draw_text('Audio: '+selected_drawer_sound[:26],box.x+13,box.bottom-206,COL['text'],SMALL)
        draw_text(editor_ui('Click > to preview'),box.x+13,box.bottom-182,COL['muted'],SMALL)
    elif obj:
        parent=next((x for x in items if x['id']==obj.get('parent_id')),None)
        draw_text('Selected: '+obj['name'][:25],box.x+13,box.bottom-206,COL['text'],SMALL)
        draw_text('Parent: '+(parent['name'] if parent else 'None')[:24],box.x+13,box.bottom-182,COL['muted'],SMALL)
    for key,r in entity_drawer_controls().items():
        if selected_drawer_sound is not None and key not in ('audio',):continue
        button(r,editor_ui({'rename':'Rename','edit':'Code Chat','parent':'Choose Parent →','visible':'Show / Hide','detach':'Detach','audio':'Sound Events'}[key]),small=True)

def entity_drawer_click(pos):
    global selected_id,entity_rename_open,entity_rename_buffer,active_panel,input_active,message
    global entity_drawer_dragging,entity_drawer_drag_offset,entity_quick_menu,selected_drawer_sound
    global entity_folder_input,entity_folder_buffer,entity_scroll,entity_move_menu,entity_row_drag
    box=entity_drawer_rect()
    if not box.collidepoint(pos):return False
    if entity_drawer_header_rect().collidepoint(pos):
        entity_drawer_dragging=True
        entity_drawer_drag_offset=(pos[0]-box.x,pos[1]-box.y)
        return True
    if pygame.Rect(box.x+12,box.y+57,126,29).collidepoint(pos):
        entity_folder_input=True;entity_folder_buffer='';return True
    if entity_move_menu:
        menu=entity_move_menu_rect()
        if menu.collidepoint(pos):
            index=(pos[1]-menu.y)//32
            options=['']+list(entity_folders)
            if 0<=index<len(options):
                folder=options[index]
                if selected_drawer_sound is not None:
                    audio_assignments[selected_drawer_sound]=folder
                elif get_selected() is not None:
                    entity_assignments[str(selected_id)]=folder
                entity_folder_closed.discard(folder)
                message='Moved to '+(folder or 'Ungrouped')
                entity_scroll=0
            entity_move_menu=False
            return True
        entity_move_menu=False
    if pygame.Rect(box.x+145,box.y+57,box.w-157,29).collidepoint(pos):
        if selected_drawer_sound is None and get_selected() is None:
            message='Select an entity or audio first';return True
        entity_move_menu=True
        return True
    track,thumb=entity_drawer_scrollbar()
    if track and track.collidepoint(pos):
        fraction=max(0,min(1,(pos[1]-track.y)/(track.h or 1)))
        entity_scroll=round(fraction*max(0,len(entity_folder_entries())-entity_visible_capacity()))
        return True
    for kind,value,r in entity_sound_rows():
        if r.collidepoint(pos):
            if kind=='folder':
                if value in entity_folder_closed:entity_folder_closed.remove(value)
                else:entity_folder_closed.add(value)
                entity_scroll=min(entity_scroll,max(0,len(entity_folder_entries())-entity_visible_capacity()))
                return True
            if kind=='sound':
                if pos[0]<r.right-36:entity_row_drag={'kind':'sound','value':value,'start':pos,'active':False}
                selected_drawer_sound=value;entity_quick_menu=False
                if pos[0]>=r.right-36:play_sound(value)
                else:message='Selected background music: '+value
            else:
                obj=value;selected_drawer_sound=None
                if pos[0]<r.right-36:entity_row_drag={'kind':'entity','value':obj['id'],'start':pos,'active':False}
                selected_id=obj['id'];message='Selected '+obj['name']
                entity_quick_menu=pos[0]>=r.right-36
            return True
    controls=entity_drawer_controls();obj=get_selected()
    if selected_drawer_sound is not None:
        if controls['audio'].collidepoint(pos):active_panel='Sounds';return True
        return True
    if obj:
        if controls['audio'].collidepoint(pos):
            open_entity_code_editor();message='Add ON CLICK SOUND PLAY name or ON START SOUND LOOP name';return True
        if controls['rename'].collidepoint(pos):
            entity_rename_open=True;entity_rename_buffer=obj['name'];return True
        if controls['edit'].collidepoint(pos):
            open_entity_code_editor()
            return True
        if controls['parent'].collidepoint(pos):
            candidates=[None]+[x for x in items if x['id']!=obj['id']]
            current=obj.get('parent_id');idx=next((j for j,x in enumerate(candidates) if (x['id'] if x else None)==current),0)
            for offset in range(1,len(candidates)+1):
                parent=candidates[(idx+offset)%len(candidates)]
                try:
                    if parent is None:detach_item(obj)
                    else:attach_item(obj,parent)
                    message='Parent: '+(parent['name'] if parent else 'None');break
                except ValueError:continue
            return True
        if controls['visible'].collidepoint(pos):obj['visible']=not obj.get('visible',True);return True
        if controls['detach'].collidepoint(pos):detach_item(obj);message='Detached '+obj['name'];return True
    # Blank space in the panel is also a drag handle, not a dead zone.
    entity_drawer_dragging=True
    entity_drawer_drag_offset=(pos[0]-box.x,pos[1]-box.y)
    return True

# Entity-specific, persistent, Python-like event command editor.
# Commands are data, never Python eval/exec; they compile to the existing ON events.
def open_entity_code_editor():
    global entity_code_open,entity_code_lines,entity_code_row,entity_code_scroll,entity_code_target,entity_quick_menu,input_active
    obj=get_selected()
    if not obj:return
    entity_code_target=obj['id']
    saved=obj.get('code_chat_source')
    if saved is None:
        saved='\n'.join('ON '+ev.upper()+' '+str(command) for ev,commands in obj.get('events',{}).items() for command in (commands if isinstance(commands,list) else [commands]))
    entity_code_lines=saved.split('\n') if saved else ['# Character events (one command per line)','ON CLICK SAY Hello!']
    entity_code_row=min(entity_code_row,len(entity_code_lines)-1)
    entity_code_scroll=0;entity_code_open=True;entity_quick_menu=False;input_active=False

def entity_code_box():
    w,h=screen.get_size();width=min(710,w-60);height=min(560,h-70)
    return pygame.Rect((w-width)//2,(h-height)//2,width,height)

def save_entity_code():
    global entity_code_open,message
    obj=next((o for o in items if o['id']==entity_code_target),None)
    if not obj:entity_code_open=False;return
    source='\n'.join(entity_code_lines)
    events={}
    in_random=False
    random_event=None
    for number,raw in enumerate(entity_code_lines,1):
        line=raw.strip()
        if not line or line.startswith('#'):continue
        upper=line.upper()
        if in_random:
            if upper=='END RANDOM DIALOGUE':
                events[random_event].append('END RANDOM DIALOGUE')
                in_random=False;random_event=None
            elif upper=='CHOICE' or upper.startswith('SAY '):
                events[random_event].append(line)
            else:
                message='Line '+str(number)+': expected CHOICE, SAY, or END RANDOM DIALOGUE';return
            continue
        parts=line.split(maxsplit=2)
        if len(parts)<3 or parts[0].upper()!='ON':
            message='Line '+str(number)+': use ON CLICK SAY Hello or ON CLICK RANDOM DIALOGUE';return
        event=parts[1].lower()
        command=parts[2]
        if event in ('key','touch','enter','exit'):
            segments=command.split(maxsplit=2)
            if event=='key':
                if len(segments)<3 or segments[1].lower() not in ('pressed','released'):
                    message='Line '+str(number)+': ON KEY E PRESSED SAY Hello';return
                event='key:'+segments[0].lower()+':'+segments[1].lower();command=segments[2]
            else:
                if len(segments)<2:
                    message='Line '+str(number)+': ON '+event.upper()+' Player SAY Hello';return
                event=event+':'+segments[0].lower();command=' '.join(segments[1:])
        if event not in ('click','start','tick','death','end','dialogue_end') and not event.startswith(('key:','touch:','enter:','exit:')):
            message='Line '+str(number)+': invalid event';return
        if command.upper()=='RANDOM DIALOGUE':
            if event!='click':message='RANDOM DIALOGUE currently requires ON CLICK';return
            in_random=True;random_event=event
        events.setdefault(event,[]).append(command)
    if in_random:
        message='Add END RANDOM DIALOGUE to finish the block';return
    obj['events']=events;obj['code_chat_source']=source
    entity_code_open=False
    save_current_project()
    message='Saved '+str(sum(map(len,events.values())))+' event commands for '+obj['name']

def draw_entity_code_editor():
    box=entity_code_box();shade=pygame.Surface(screen.get_size(),pygame.SRCALPHA)
    shade.fill((9,8,15,190));screen.blit(shade,(0,0))
    pygame.draw.rect(screen,COL['panel'],box,border_radius=12)
    pygame.draw.rect(screen,COL['accent'],box,2,border_radius=12)
    obj=next((o for o in items if o['id']==entity_code_target),None)
    draw_text('CODE CHAT  /  '+(obj['name'] if obj else 'Entity'),box.x+20,box.y+18,COL['accent'],BIG)
    draw_text('Write ON commands, one per line. Saved to this entity.',box.x+20,box.y+54,COL['muted'],SMALL)
    area=pygame.Rect(box.x+20,box.y+84,box.w-40,box.h-162)
    pygame.draw.rect(screen,(22,23,33),area,border_radius=5)
    previous=screen.get_clip();screen.set_clip(area.inflate(-8,-8))
    visible=max(1,(area.h-12)//24)
    for row in range(entity_code_scroll,min(len(entity_code_lines),entity_code_scroll+visible)):
        yy=area.y+8+(row-entity_code_scroll)*24
        if row==entity_code_row:pygame.draw.rect(screen,(52,49,67),pygame.Rect(area.x+4,yy-2,area.w-8,24))
        draw_text(str(row+1).rjust(3),area.x+9,yy,COL['muted'],SMALL)
        draw_text(entity_code_lines[row][:100],area.x+52,yy,COL['text'],SMALL)
        if row==entity_code_row and pygame.time.get_ticks()//500%2==0:
            font=interface_font(SMALL,entity_code_lines[row]);cx=min(area.right-15,area.x+52+font.size(entity_code_lines[row])[0])
            pygame.draw.line(screen,COL['accent'],(cx,yy),(cx,yy+17),2)
    screen.set_clip(previous)
    draw_text('ON CLICK RANDOM DIALOGUE / CHOICE / SAY / END RANDOM DIALOGUE',box.x+20,box.bottom-68,COL['muted'],SMALL)
    button(pygame.Rect(box.right-250,box.bottom-47,135,34),'Save Events',small=True)
    button(pygame.Rect(box.right-106,box.bottom-47,86,34),'Cancel',small=True)

def entity_code_click(pos):
    global entity_code_row,entity_code_scroll,entity_code_open
    box=entity_code_box()
    if pygame.Rect(box.right-250,box.bottom-47,135,34).collidepoint(pos):save_entity_code();return
    if pygame.Rect(box.right-106,box.bottom-47,86,34).collidepoint(pos):entity_code_open=False;return
    area=pygame.Rect(box.x+20,box.y+84,box.w-40,box.h-162)
    if area.collidepoint(pos):entity_code_row=min(len(entity_code_lines)-1,entity_code_scroll+max(0,(pos[1]-area.y-8)//24))

def entity_code_key(event):
    global entity_code_row,entity_code_scroll,entity_code_open
    if event.key==pygame.K_ESCAPE:entity_code_open=False
    elif event.key==pygame.K_RETURN:
        entity_code_lines.insert(entity_code_row+1,'');entity_code_row+=1
    elif event.key==pygame.K_BACKSPACE:
        if entity_code_lines[entity_code_row]:entity_code_lines[entity_code_row]=entity_code_lines[entity_code_row][:-1]
        elif len(entity_code_lines)>1:
            entity_code_lines.pop(entity_code_row);entity_code_row=max(0,entity_code_row-1)
    elif event.key==pygame.K_UP:entity_code_row=max(0,entity_code_row-1)
    elif event.key==pygame.K_DOWN:entity_code_row=min(len(entity_code_lines)-1,entity_code_row+1)
    elif event.key==pygame.K_TAB:entity_code_lines[entity_code_row]+='    '
    elif event.unicode and event.unicode.isprintable():entity_code_lines[entity_code_row]+=event.unicode
    visible=max(1,(entity_code_box().h-162-12)//24)
    entity_code_scroll=max(0,min(entity_code_scroll,entity_code_row)) if entity_code_row<entity_code_scroll else max(entity_code_scroll,entity_code_row-visible+1)

def quick_menu_rects():
    box=entity_drawer_rect();x=box.right+7
    if x+168>screen.get_width():x=box.x-175
    y=max(44,min(box.y+92,screen.get_height()-220))
    return [(name,pygame.Rect(x,y+j*39,168,35)) for j,name in enumerate(('Code Chat','Add Sound','Add Animation','Add Overlay'))]

def draw_quick_menu():
    for name,rect in quick_menu_rects():button(rect,name,small=True)

def quick_menu_click(pos):
    global entity_quick_menu,active_panel,message
    for name,rect in quick_menu_rects():
        if rect.collidepoint(pos):
            entity_quick_menu=False
            if name=='Code Chat':open_entity_code_editor()
            elif name=='Add Sound':active_panel='Sounds';message='Import sounds, then use ON CLICK SOUND PLAY name in entity Code Chat.'
            elif name=='Add Animation':active_panel='Animation';message='Select New Animation, then Add Frame.'
            elif name=='Add Overlay':active_panel='Overlays';message='Add an overlay and set its parent to this character.'
            else:open_entity_code_editor()
            return True
    entity_quick_menu=False
    return False

def sidebar_buttons(side):
    names=['Scenes','Story Flow','Import Character','Import Background','Import Video','Import Text','Import Objects','Import UI/Buttons','Sounds','Overlays','Code Chat','Python Script','Coding Manual','Information']
    return [(name,pygame.Rect(side.x+12,side.y+44+j*23,side.w-24,22)) for j,name in enumerate(names)]

def panel_buttons(side):
    y=side.y+485
    if active_panel=='Python Script':
        return [('Run Python',pygame.Rect(side.x+12,side.bottom-55,130,33)),('Clear',pygame.Rect(side.x+151,side.bottom-55,78,33))]
    if active_panel=='Coding Manual':
        return [('Edit Manual File',pygame.Rect(side.x+12,side.bottom-55,156,33)),('Reload',pygame.Rect(side.x+176,side.bottom-55,86,33))]
    if active_panel=='Code Chat':
        return [('Run',pygame.Rect(side.x+side.w-84,side.bottom-55,68,33))]
    if active_panel=='Animation':
        return [('New Animation',pygame.Rect(side.x+14,side.y+515,side.w-28,31)),
                ('Add Frame',pygame.Rect(side.x+14,side.y+552,side.w-28,31)),
                ('Next Animation',pygame.Rect(side.x+14,side.y+589,side.w-28,31)),
                ('Play / Pause',pygame.Rect(side.x+14,side.y+626,side.w-28,31))]
    if active_panel=='Sounds':
        return [('Import Sound',pygame.Rect(side.x+14,side.y+505,side.w-28,32)),('Play Sound',pygame.Rect(side.x+14,side.y+546,side.w-28,32)),('Stop Sounds',pygame.Rect(side.x+14,side.y+587,side.w-28,32))]
    if active_panel=='Overlays':
        return [('New Overlay',pygame.Rect(side.x+14,side.y+505,side.w-28,32)),('Set Parent (Code Chat)',pygame.Rect(side.x+14,side.y+546,side.w-28,32)),('Detach Selected',pygame.Rect(side.x+14,side.y+587,side.w-28,32))]
    if active_panel=='Background Size':
        return [('Width -',pygame.Rect(side.x+12,y,74,32)),('Width +',pygame.Rect(side.x+92,y,74,32)),('Height -',pygame.Rect(side.x+12,y+40,74,32)),('Height +',pygame.Rect(side.x+92,y+40,74,32))]
    return []

def sidebar_click(name):
    global active_panel,input_active,message
    if name=='Import Character':
        p=choose_file('Choose character PNG')
        if p:new_item('character',p)
    elif name=='Import Background':import_background()
    elif name=='Import Video':import_video()
    elif name=='Import Text':
        new_item('text',text='Your text here')
        active_panel='Edit Text'
        input_text_placeholder()
        input_active=True
    elif name=='Import Objects':
        p=choose_file('Choose object PNG')
        if p:new_item('object',p)
    elif name=='Import UI/Buttons':
        p=choose_file('Choose a PNG image for your button')
        if p:new_item('button',p)
    else:
        if name=='Code Chat' and get_selected() is not None:
            open_entity_code_editor()
            return
        active_panel=name
        input_active=False

def create_animation_clip(name):
    global message
    item=get_selected()
    if not item or item['kind'] not in ('character','object','button'):
        message='Select an imported character, object, or button first';return
    name=name.strip()
    if not name or len(name)>28:message='Animation name must be 1-28 characters';return
    clips=item.setdefault('animations',{})
    if name in clips:message='That animation already exists';return
    clips[name]={'frames':[],'fps':8,'loop':True}
    item['active_animation']=name
    message='Created '+name+'. Click Add Frame to import PNGs.'

def add_animation_frame():
    global message
    i=get_selected()
    if not i or i['kind'] not in ('character','object','button'):
        message='Select a character, object, or button first'
        return
    p=choose_file('Choose next animation frame PNG')
    if p:
        try:
            pygame.image.load(p).convert_alpha()
            clip=selected_animation(i)
            if clip is None:
                message='Create or select a named animation first';return
            clip['frames'].append(p)
            message='Frame added to '+i['active_animation']+' ('+str(len(clip['frames']))+' frames)'
        except (OSError,pygame.error) as e:message='Frame error: '+str(e)

def draw_item(i,canvas):
    if not i.get('visible',True):return
    r=rect_for(i,canvas)
    transition=i.get('_transition')
    alpha=255
    if transition:
        elapsed=pygame.time.get_ticks()-transition['start']
        t=min(1.0,max(0.,elapsed/max(1,transition['duration'])))
        effect=transition['effect']
        if effect=='FADEIN':alpha=int(255*t)
        elif effect=='FADEOUT':alpha=int(255*(1-t))
        elif effect=='SLIDELEFT':r.x+=int((1-t)*max(60,canvas.w//3))
        elif effect=='SLIDERIGHT':r.x-=int((1-t)*max(60,canvas.w//3))
        if t>=1:
            i.pop('_transition',None)
            if effect=='FADEOUT':i['visible']=False;return
    def blit_image(img,rect):
        surf=pygame.transform.smoothscale(img,(max(1,rect.w),max(1,rect.h)))
        if alpha<255:
            surf=surf.copy();surf.set_alpha(alpha)
        screen.blit(surf,rect)
    if i['kind'] in ('character','object'):
        img=i.get('image')
        clip=selected_animation(i)
        if clip and i.get('_anim_playing') and clip.get('frames'):
            paths=clip['frames']
            step=int((pygame.time.get_ticks()-i.get('_anim_started',pygame.time.get_ticks()))*max(1,min(60,int(clip.get('fps',8))))/1000)
            if not clip.get('loop',True) and step>=len(paths):
                i['_anim_playing']=False
                step=len(paths)-1
            p=paths[step%len(paths)]
            if i.get('_current_frame_path')!=p:
                try:i['_frame_image']=pygame.image.load(p).convert_alpha();i['_current_frame_path']=p
                except (OSError,pygame.error):pass
            img=i.get('_frame_image',img)
        if img:blit_image(img,r)
        else:pygame.draw.rect(screen,COL['red'],r,2)
    elif i['kind']=='button':
        clip=selected_animation(i)
        img=i.get('image')
        if clip and i.get('_anim_playing') and clip.get('frames'):
            paths=clip['frames'];step=int((pygame.time.get_ticks()-i.get('_anim_started',0))*max(1,min(60,int(clip.get('fps',8))))/1000)
            if not clip.get('loop',True) and step>=len(paths):i['_anim_playing']=False;step=len(paths)-1
            path=paths[step%len(paths)]
            if i.get('_current_frame_path')!=path:
                try:i['_frame_image']=pygame.image.load(path).convert_alpha();i['_current_frame_path']=path
                except (OSError,pygame.error):pass
            img=i.get('_frame_image',img)
        if img:
            blit_image(img,r)
        else:
            hovered=mode=='play' and r.collidepoint(pygame.mouse.get_pos())
            pygame.draw.rect(screen,COL['selected'] if hovered else COL['accent'],r,border_radius=7)
            pygame.draw.rect(screen,COL['line'],r,2 if hovered else 1,border_radius=7)
            t=FONT.render(display_safe(i['text'][:30]),True,parse_hex_color(i.get('text_color','#FFFFFF')));screen.blit(t,t.get_rect(center=r.center))
    else:
        t=FONT.render(display_safe(i['text'][:75]),True,parse_hex_color(i.get('text_color','#FFFFFF')));screen.blit(t,(r.x+3,r.y+4))
    if mode=='edit' and i['id']==selected_id:
        pygame.draw.rect(screen,COL['green'],r,2)
        pygame.draw.rect(screen,COL['green'],pygame.Rect(r.right-8,r.bottom-8,12,12))

MANUAL = [
    'HOW TO CODE IN 2D APPMAKER',
    'V22: TYPEWRITER 35 (letters per second)',
    'TYPEWRITER OFF (instant dialogue)',
    'Select any imported image, then:',
    'TRANSITION FADEIN 0.8',
    'TRANSITION FADEOUT 0.8',
    'TRANSITION SLIDELEFT 1.2',
    'TRANSITION SLIDERIGHT 1.2',
    'Select a button: ON CLICK SAY Hello!',
    'The Code Chat uses SIMPLE COMMANDS,',
    'not raw Python code.',
    '',
    'APP COMMANDS:',
    'HELP',
    'MOVE RIGHT 50',
    'MOVE Character 1 LEFT 30',
    'SAY Hello there!',
    'SAY Character 1 Welcome!',
    'SET has_key yes',
    'IF has_key yes SAY Door opens!',
    'HIDE  /  SHOW  /  CLEAR',
    'PLAY  /  STOP',
    '',
    'PYTHON BASICS (for learning):',
    '1. Print a message:',
    'print("Hello!")',
    '',
    '2. Store a value:',
    'health = 100',
    'name = "Hero"',
    '',
    '3. Check a condition:',
    'if health > 0:',
    '    print("Still alive")',
    '',
    '4. Repeat with a loop:',
    'for step in range(5):',
    '    print(step)',
    '',
    '5. Define a function:',
    'def greet(name):',
    '    print("Hi", name)',
    '',
    '6. Use a list:',
    'items = ["key", "map"]',
    'print(items[0])',
    '',
    'IMPORTANT:',
    'Python examples are lessons only.',
    'Paste APP COMMANDS in Code Chat.',
    'Python Script runs trusted Python code.',
    'Never run scripts from strangers.',
    'Use api.move(id, dx, dy), api.say(text).',
    'api.change_scene("Scene 2")',
    'api.set_movement(True / False)',
    'api.choice("Help her", "Scene 2")',
    'Edit coding_manual.txt to add lessons.',
]

def open_manual_file():
    global message
    load_manual()
    try:
        subprocess.Popen(['open', manual_path])
        message='Opened coding_manual.txt; save edits and press Reload.'
    except Exception as e:
        message='Open failed: '+str(e)+' | '+manual_path

def load_manual():
    global MANUAL,message
    if not os.path.isfile(manual_path):
        try:
            with open(manual_path,'w',encoding='utf-8') as f:
                f.write('\n'.join(MANUAL)+'\n')
        except OSError as e:
            message='Manual creation failed: '+str(e)
            return
    try:
        with open(manual_path,encoding='utf-8') as f:
            MANUAL=f.read().splitlines()
        message='Manual loaded from coding_manual.txt'
    except (OSError,UnicodeError) as e:
        message='Manual load failed: '+str(e)


class ScriptAPI:
    """Convenient app operations for developer-authored Python scripts."""
    def character(self, number):
        item=next((i for i in items if i['kind']=='character' and
                   i['name'].lower()==f'character {number}'.lower()),None)
        if item is None:
            raise ValueError(f'Character {number} not found')
        return item

    def move(self, number, dx=0, dy=0):
        character=self.character(number)
        character['x']+=int(dx)
        character['y']+=int(dy)

    def say(self, text):
        set_scene_dialogue(text)

    def change_scene(self, name):
        return change_scene(str(name))

    def set_movement(self, enabled):
        global movement_enabled
        movement_enabled=bool(enabled)

    def choice(self, label, target_scene):
        scene_choices.append({'label':str(label),'target':str(target_scene)})

    def set_flag(self, name, value):
        story_flags[str(name)]=value

    def get_flag(self, name, default=None):
        return story_flags.get(str(name),default)

    def show(self, number, visible=True):
        self.character(number)['visible']=bool(visible)


def run_python_script():
    global message,script_confirm
    if not script_confirm:
        script_confirm=True
        message='Python runs with your computer permissions! Click Run Python again ONLY for code you wrote/trust.'
        return
    script_confirm=False
    source='\n'.join(script_lines)
    try:
        # This is real Python, not a security sandbox. Only run developer-trusted code.
        exec(compile(source,'<2d appmaker Python Script>','exec'),{'api':ScriptAPI(),'__builtins__':__builtins__})
        message='Python script finished successfully.'
    except Exception as exc:
        message=f'Python error: {type(exc).__name__}: {exc}'
        print(traceback.format_exc())


def script_editor_rect(side):
    return pygame.Rect(side.x+12,side.y+450,side.w-24,max(65,side.h-515))


def draw_script_editor(side):
    area=script_editor_rect(side)
    draw_text('PYTHON SCRIPT - optional',area.x,area.y-39,COL['accent'],SMALL)
    draw_text('Advanced: trusted Python only',area.x,area.y-18,COL['muted'],SMALL)
    pygame.draw.rect(screen,(21,25,36),area,border_radius=5)
    pygame.draw.rect(screen,COL['line'],area,2,border_radius=5)
    old=screen.get_clip();screen.set_clip(area)
    max_lines=max(1,(area.h-12)//19)
    for idx in range(script_scroll,min(len(script_lines),script_scroll+max_lines)):
        y=area.y+6+(idx-script_scroll)*19
        line=script_lines[idx]
        draw_text(f'{idx+1:>2}  '+line[:75],area.x+6,y,COL['text'],SMALL)
        if input_active and active_panel=='Python Script' and idx==script_row:
            prefix=SMALL.render(f'{idx+1:>2}  '+line[:script_col],True,COL['text'])
            x=min(area.right-7,area.x+6+prefix.get_width())
            pygame.draw.line(screen,COL['green'],(x,y),(x,y+16),2)
    screen.set_clip(old)
    for name,r in panel_buttons(side):button(r,name,small=True)


load_manual()

def manual_view_rect(side,y):
    return pygame.Rect(side.x+10,y+8,side.w-20,max(60,side.bottom-y-137))

def manual_max_scroll_x(side):
    # Measure actual rendered pixel widths; this works with long Markdown tables.
    available=max(30,side.w-38)
    return max(0,max((SMALL.size(line)[0] for line in MANUAL),default=0)-available)

def manual_scrollbar_rect(side,y):
    area=manual_view_rect(side,y)
    return pygame.Rect(area.x+5,area.bottom+5,area.w-10,12)

def draw_manual(side,y):
    global manual_scroll_x
    area=manual_view_rect(side,y)
    pygame.draw.rect(screen,(22,27,39),area,border_radius=5)
    old=screen.get_clip()
    screen.set_clip(area)
    for k,line in enumerate(MANUAL):
        yy=area.y+9+(k-manual_scroll)*22
        if yy+18<area.y or yy>area.bottom:continue
        color=COL['accent'] if line.endswith(':') or line=='HOW TO CODE IN 2D APPMAKER' else COL['text']
        draw_text(line,area.x+8-manual_scroll_x,yy,color,SMALL)
    screen.set_clip(old)
    track=manual_scrollbar_rect(side,y)
    pygame.draw.rect(screen,(34,38,52),track,border_radius=5)
    maximum=manual_max_scroll_x(side)
    manual_scroll_x=max(0,min(maximum,manual_scroll_x))
    if maximum:
        longest=maximum+max(30,side.w-38)
        thumb_w=max(24,int(track.w*max(30,side.w-38)/longest))
        thumb_x=track.x+int((track.w-thumb_w)*manual_scroll_x/maximum)
    else:
        thumb_w=track.w;thumb_x=track.x
    pygame.draw.rect(screen,COL['accent'],pygame.Rect(thumb_x,track.y,thumb_w,track.h),border_radius=5)
    draw_text('Shift+wheel: horizontal',area.x+4,track.bottom+8,COL['muted'],SMALL)
    for name,r in panel_buttons(side):button(r,name,small=True)

def draw_scene_panel(side):
    y=side.y+410
    draw_text('CURRENT: '+current_scene[:23],side.x+14,y+6,COL['accent'],SMALL)
    draw_text('Scenes: '+str(len(scene_order)),side.x+14,y+28,COL['muted'],SMALL)
    r=scene_ui_rects(side)
    for key,label in [('new','+ New Scene'),('next','Next Scene'),
                      ('type','Type: '+scene_type),
                      ('movement','Walking: '+('ON' if movement_enabled else 'OFF')),
                      ('player','Player: '+(str(player_id) if player_id else 'Select character')),
                      ('dialogue','Set dialogue from Code Chat'),
                      ('choice','Add choice via Code Chat'),
                      ('collision','Collisions: '+('ON' if collision_enabled else 'OFF')),
                      ('timeline','Dialogue lines: '+str(len(dialogue_lines)))]:
        button(r[key],label,small=True)
    # Keep the panel footer empty: the last scene control needs this space.

def draw():
    if ONLINE_AUTH_SCREEN:
        online_draw_auth()
        return
    if app_page=='home':
        draw_home()
        return
    if app_page=='community':
        community_draw(screen,(FONT,SMALL,BIG),COL,profile_name)
        return
    screen.fill(COL['bg'])
    canvas_outer,side=layout();canvas=canvas_rect()
    update_play_camera()
    draw_text('2d appmaker',17,12,font=BIG)
    draw_text(profile_name+' / '+project_name,180,19,COL['muted'],SMALL)
    button(pygame.Rect(screen.get_width()-155,8,137,32),'My Projects',small=True)
    for label,r in toolbar_buttons():button(r,label,mode=='play' and label=='Game Mode')
    pygame.draw.rect(screen,COL['panel'],canvas_outer,border_radius=8)
    pygame.draw.rect(screen,COL['canvas'],canvas,border_radius=4)
    old=screen.get_clip();screen.set_clip(canvas)
    if background:
        bx,by=world_to_screen(bg_rect[0],bg_rect[1],canvas)
        z,_,_=view_transform(canvas)
        r=pygame.Rect(round(bx),round(by),max(1,round(bg_rect[2]*z)),max(1,round(bg_rect[3]*z)))
        screen.blit(pygame.transform.smoothscale(background,(max(1,r.w),max(1,r.h))),r)
    # V60: Draw ONLY the camera/game viewport outline, not the full map bounds.
    # The rectangle represents the area visible at one time in the game.
    # Keep it independent of imported backgrounds and selection handles.
    if mode != 'play':
        vx,vy=world_to_screen(0,0,canvas)
        z,_,_=view_transform(canvas)
        camera_frame=pygame.Rect(round(vx),round(vy),max(1,round(canvas.w*z)),max(1,round(canvas.h*z)))
        pygame.draw.rect(screen,COL['accent'],camera_frame,2)
    for i in items:
        if not i.get('overlay'):draw_item(i,canvas)
    for i in items:
        if i.get('overlay'):draw_item(i,canvas)
    if mode=='play' and dialogue:
        height=int(dialogue_style.get('height',90))
        box=pygame.Rect(canvas.x+20,canvas.bottom-height-22,canvas.w-40,height)
        surface=pygame.Surface(box.size,pygame.SRCALPHA)
        pygame.draw.rect(surface,(*parse_hex_color(dialogue_style.get('bg','#191C2A')),int(dialogue_style.get('opacity',235))),surface.get_rect(),border_radius=9)
        image_path=dialogue_style.get('image')
        if image_path and os.path.isfile(image_path):
            try:
                img=pygame.image.load(image_path).convert_alpha()
                img=pygame.transform.smoothscale(img,box.size)
                img.set_alpha(int(dialogue_style.get('opacity',235)))
                screen.blit(img,box.topleft)
            except (pygame.error,OSError):screen.blit(surface,box.topleft)
        else:screen.blit(surface,box.topleft)
        if not image_path:pygame.draw.rect(screen,parse_hex_color(dialogue_style.get('border','#F5A3C9')),box,2,border_radius=9)
        shown=len(dialogue) if typewriter_cps==0 else max(0,min(len(dialogue),int((pygame.time.get_ticks()-dialogue_started_ms)*typewriter_cps/1000)))
        draw_text(dialogue[:shown][:90],box.x+15,box.y+18,parse_hex_color(dialogue_style.get('text','#FFFFFF')))
        if dialogue_lines:draw_text('Click or Space for next  •  '+str(dialogue_index+1)+'/'+str(len(dialogue_lines)),box.x+15,box.bottom-25,COL['muted'],SMALL)
    if mode=='play' and scene_type=='choice' and scene_choices:
        choice_open_area=pygame.Rect(canvas.x+max(10,canvas.w//5),canvas.y+max(40,canvas.h//4),
                                     max(170,canvas.w*3//5),min(canvas.h-90,80+len(scene_choices)*47))
        pygame.draw.rect(screen,(48,36,49),choice_open_area,border_radius=12)
        pygame.draw.rect(screen,COL['accent'],choice_open_area,3,border_radius=12)
        draw_text('Choose your response:',choice_open_area.x+15,choice_open_area.y+12,COL['text'])
        for j,ch in enumerate(scene_choices[:7]):
            cr=pygame.Rect(choice_open_area.x+15,choice_open_area.y+45+j*47,choice_open_area.w-30,39)
            button(cr,ch['label'][:50],small=True)
    screen.set_clip(old)
    draw_text('GAME PREVIEW • Camera follows player' if mode=='play' else 'MAP  '+str(map_width)+'x'+str(map_height)+'  •  Zoom '+str(round(editor_zoom*100))+'%  •  Wheel: zoom  •  Middle/Space+drag: pan  •  Ctrl+0: reset',canvas.x+4,canvas.y+5,COL['muted'],SMALL)
    pygame.draw.rect(screen,COL['panel'],side,border_radius=8)
    draw_text('TOOLS',side.x+15,side.y+13,font=BIG)
    for name,r in sidebar_buttons(side):button(r,'Background Music' if name=='Sounds' else name,name==active_panel)
    y=side.y+410
    pygame.draw.line(screen,COL['line'],(side.x+12,y),(side.right-12,y),1)
    if active_panel=='Scenes':
        draw_scene_panel(side)
    elif active_panel=='Story Flow':
        draw_story_flow(side)
    elif active_panel=='Python Script':
        draw_script_editor(side)
    elif active_panel=='Entity Events':
        owner=get_selected()
        scripts=owner.get('events',{}) if owner else scene_events
        draw_text('TARGET: '+(owner['name'] if owner else 'Background'),side.x+13,y+12,COL['accent'],SMALL)
        draw_text('Select item, then Code Chat:',side.x+13,y+39,COL['text'],SMALL)
        for k,line in enumerate(['ON CLICK SAY Hello!', 'ON CLICK AFTER 2 PLAY ANIMATION Attack','ON TICK CHANCE 5 SHOW','ON DEATH DISAPPEAR','EVENTS / CLEAREVENTS']):
            draw_text(line,side.x+13,y+67+k*22,COL['muted'],SMALL)
        draw_text('Sounds: ON CLICK SOUND PLAY greeting',side.x+13,y+183,COL['muted'],SMALL)
        draw_text('ON CLICK AFTER 2 SOUND PLAY laugh',side.x+13,y+204,COL['muted'],SMALL)
        draw_text('Saved commands:',side.x+13,y+239,COL['accent'],SMALL)
        for k,(ev,cmds) in enumerate(list(scripts.items())[:5]):
            draw_text((ev+': '+str(cmds)[:29])[:40],side.x+13,y+262+k*20,COL['text'],SMALL)
    elif active_panel=='Code Chat':
        draw_text('COMMAND CONSOLE',side.x+13,y+12,COL['muted'],SMALL)
        lines=chat_history[-5:]
        for k,line in enumerate(lines):
            draw_text(line[:37],side.x+14,y+39+k*21,COL['red'] if line.startswith('Command error') else COL['text'],SMALL)
        entry=pygame.Rect(side.x+12,side.bottom-55,side.w-110,33)
        pygame.draw.rect(screen,(21,25,36),entry,border_radius=5)
        pygame.draw.rect(screen,COL['accent'] if input_active else COL['line'],entry,2,border_radius=5)
        draw_text((input_text[-23:] or 'Type command...')+('|' if input_active and pygame.time.get_ticks()//500%2==0 else ''),entry.x+7,entry.y+7,COL['text'] if input_text else COL['muted'],SMALL)
        button(pygame.Rect(side.right-84,side.bottom-55,68,33),'Run',small=True)
    elif active_panel=='Edit Text':
        draw_text('EDIT SELECTED TEXT',side.x+14,y+12,COL['muted'],SMALL)
        draw_text('Click field, type, then Enter.',side.x+14,y+42,COL['text'],SMALL)
        entry=pygame.Rect(side.x+12,y+80,side.w-24,38)
        pygame.draw.rect(screen,(21,25,36),entry,border_radius=5)
        pygame.draw.rect(screen,COL['accent'],entry,2,border_radius=5)
        draw_text(input_text[-34:] or 'Type your world text here...',entry.x+8,entry.y+9,COL['text'] if input_text else COL['muted'],SMALL)
        draw_text('Tip: Select a text item, then Edit > Edit Text.',side.x+14,y+135,COL['muted'],SMALL)
    elif active_panel=='Coding Manual':
        draw_manual(side,y)
    elif active_panel=='Information':
        for n,line in enumerate(['2d appmaker • prototype','Characters: '+str(sum(i['kind']=='character' for i in items)),
                                 'Objects: '+str(sum(i['kind']=='object' for i in items)),
                                 'Mode: '+mode,'Selected: '+(get_selected()['name'] if get_selected() else 'None'),
                                 'Video: '+(os.path.basename(video_path) if video_path else 'None'),
                                 'Files > Save Project to save.']):draw_text(line[:37],side.x+14,y+15+n*27,COL['text'],SMALL)
    elif active_panel=='Animation':
        i=get_selected()
        valid=bool(i and i['kind'] in ('character','object','button'))
        draw_text(('Selected: '+i['name'][:20]) if valid else 'Select a character on the canvas',side.x+14,y+12,COL['text'],SMALL)
        clip=selected_animation(i) if valid else None
        draw_text('Active: '+(i.get('active_animation') or 'None')[:21] if valid else 'No item selected',side.x+14,y+37,COL['muted'],SMALL)
        draw_text('Frames: '+str(len(clip.get('frames',[])) if clip else 0)+'  Clips: '+str(len(i.get('animations',{})) if valid else 0),side.x+14,y+62,COL['muted'],SMALL)
        for name,r in panel_buttons(side):button(r,name,small=True)
        draw_text('ANIM FPS 12 / ANIM LOOP OFF',side.x+14,side.y+674,COL['muted'],SMALL)
        if not valid:draw_text('Select an image item first.',side.x+14,side.y+697,COL['accent'],SMALL)
    elif active_panel=='Sounds':
        draw_text('BACKGROUND MUSIC / AUDIO',side.x+14,y+12,COL['accent'],SMALL)
        for j,name in enumerate(list(scene_sounds)[-3:]):
            draw_text(name[:30],side.x+14,y+42+j*21,COL['text'],SMALL)
        draw_text('Select an entity > Sound Events',side.x+14,side.y+656,COL['muted'],SMALL)
        draw_text('ON CLICK SOUND PLAY name',side.x+14,side.y+679,COL['muted'],SMALL)
        draw_text('ON CLICK AFTER 2 SOUND PLAY name',side.x+14,side.y+702,COL['muted'],SMALL)
        for name,r in panel_buttons(side):button(r,name,small=True)
    elif active_panel=='Overlays':
        owner=get_selected()
        draw_text('Selected: '+(owner['name'] if owner else 'None'),side.x+14,y+12,COL['text'],SMALL)
        draw_text('Overlay images render above characters.',side.x+14,y+40,COL['muted'],SMALL)
        draw_text('PARENT Character 1 in Code Chat',side.x+14,y+68,COL['muted'],SMALL)
        draw_text('Select child, then attach to parent.',side.x+14,y+94,COL['muted'],SMALL)
        for name,r in panel_buttons(side):button(r,name,small=True)
    elif active_panel=='Background Size':
        draw_text('Background: '+str(bg_rect[2])+' × '+str(bg_rect[3]),side.x+14,y+12)
        for name,r in panel_buttons(side):button(r,name,small=True)
    draw_text(message[:145],17,screen.get_height()-16,COL['muted'],SMALL)
    if animation_name_input:
        veil=pygame.Surface(screen.get_size(),pygame.SRCALPHA);veil.fill((15,10,20,190));screen.blit(veil,(0,0))
        modal=pygame.Rect(screen.get_width()//2-235,screen.get_height()//2-95,470,190)
        pygame.draw.rect(screen,COL['panel'],modal,border_radius=15)
        pygame.draw.rect(screen,COL['accent'],modal,2,border_radius=15)
        draw_text('New named animation',modal.x+22,modal.y+20,COL['accent'],BIG)
        draw_text(animation_name_buffer or 'Type name, e.g. Walk or Attack',modal.x+22,modal.y+79,COL['text'])
        draw_text('Enter to create  |  Esc to cancel',modal.x+22,modal.y+145,COL['muted'],SMALL)
    if scene_name_input:
        veil=pygame.Surface(screen.get_size(),pygame.SRCALPHA)
        veil.fill((15,10,20,190));screen.blit(veil,(0,0))
        modal=pygame.Rect(screen.get_width()//2-235,screen.get_height()//2-105,470,210)
        pygame.draw.rect(screen,COL['panel'],modal,border_radius=15)
        pygame.draw.rect(screen,COL['accent'],modal,2,border_radius=15)
        draw_text('Create a new scene',modal.x+24,modal.y+20,COL['accent'],BIG)
        draw_text('Scene name',modal.x+24,modal.y+65,COL['muted'],SMALL)
        field=pygame.Rect(modal.x+24,modal.y+88,modal.w-48,43)
        pygame.draw.rect(screen,(31,27,37),field,border_radius=6)
        pygame.draw.rect(screen,COL['accent'],field,2,border_radius=6)
        draw_text((scene_name_buffer or 'Type your scene name...')+('|' if scene_name_buffer else ''),field.x+12,field.y+10,COL['text'] if scene_name_buffer else COL['muted'])
        draw_text('Press Enter to create · Esc to cancel',modal.x+24,modal.y+155,COL['muted'],SMALL)
    if entity_drawer_open and mode=='edit':
        draw_entity_drawer()
        if entity_quick_menu:draw_quick_menu()
    if entity_code_open:draw_entity_code_editor()
    if entity_folder_input:
        shade=pygame.Surface(screen.get_size(),pygame.SRCALPHA);shade.fill((9,8,15,170));screen.blit(shade,(0,0))
        modal=pygame.Rect(screen.get_width()//2-245,screen.get_height()//2-85,490,170)
        pygame.draw.rect(screen,COL['panel'],modal,border_radius=12)
        pygame.draw.rect(screen,COL['accent'],modal,3,border_radius=12)
        draw_text(editor_ui('Create entity folder'),modal.x+20,modal.y+18,COL['accent'],BIG)
        draw_text((entity_folder_buffer or editor_ui('Folder name...'))+'|',modal.x+20,modal.y+75,COL['text'])
        draw_text(editor_ui('Enter: create  ·  Esc: cancel'),modal.x+20,modal.y+132,COL['muted'],SMALL)
    if entity_rename_open:
        modal=pygame.Rect(screen.get_width()//2-245,screen.get_height()//2-85,490,170)
        pygame.draw.rect(screen,COL['panel'],modal,border_radius=12)
        pygame.draw.rect(screen,COL['accent'],modal,3,border_radius=12)
        draw_text('Rename selected entity',modal.x+20,modal.y+18,COL['accent'],BIG)
        draw_text(entity_rename_buffer+'|',modal.x+20,modal.y+75,COL['text'])
        draw_text('Enter: save   •   Esc: cancel',modal.x+20,modal.y+132,COL['muted'],SMALL)
    if file_menu:
        for j,label in enumerate(['Save Project','Open Project','Export Playable ZIP','Build Desktop Game']):button(pygame.Rect(108,84+j*37,175,35),label,small=True)
    if edit_menu:
        for j,label in enumerate(['Delete Selected','Background Size','Edit Text']):button(pygame.Rect(198,84+j*37,170,35),label,small=True)
    pygame.display.flip()

def click(pos):
    if pygame.Rect(screen.get_width()-155,8,137,32).collidepoint(pos):
        go_home();return
    global selected_id,dragging,drag_offset,resize_drag,input_active,file_menu,edit_menu,mode,active_panel,message,animation_play,animation_name_input,animation_name_buffer,dialogue,script_row,script_col,script_confirm,scene_name_input,scene_name_buffer,movement_enabled,player_id,collision_enabled,dialogue_index,dialogue_started_ms,entity_drawer_open
    if scene_name_input or entity_rename_open or entity_folder_input:
        return
    if active_panel=='Coding Manual':
        bar=manual_scrollbar_rect(layout()[1],layout()[1].y+410)
        if bar.inflate(0,12).collidepoint(pos):
            global manual_hdrag,manual_scroll_x
            manual_hdrag=True
            maximum=manual_max_scroll_x(layout()[1])
            manual_scroll_x=max(0,min(maximum,int((pos[0]-bar.x)/max(1,bar.w)*maximum)))
            return
    if file_menu:
        if pygame.Rect(108,84,175,35).collidepoint(pos):save_project()
        elif pygame.Rect(108,121,175,35).collidepoint(pos):load_project()
        elif pygame.Rect(108,158,175,35).collidepoint(pos):export_game()
        elif pygame.Rect(108,195,175,35).collidepoint(pos):build_desktop_game()
        file_menu=False;return
    if edit_menu:
        if pygame.Rect(198,84,170,35).collidepoint(pos):
            if get_selected():remove_selected_with_children();selected_id=None;message='Item deleted'
        elif pygame.Rect(198,121,170,35).collidepoint(pos):active_panel='Background Size'
        elif pygame.Rect(198,158,170,35).collidepoint(pos):
            if get_selected() and get_selected()['kind'] in ('text','button'):
                active_panel='Edit Text';input_active=True;input_text_placeholder()
        edit_menu=False;return
    if entity_folder_input or entity_rename_open:
        return
    if entity_quick_menu and quick_menu_click(pos):return
    if entity_drawer_open and mode=='edit' and entity_drawer_rect().collidepoint(pos):
        entity_drawer_click(pos)
        return
    for name,r in toolbar_buttons():
        if r.collidepoint(pos):
            if name=='Import':import_background()
            elif name=='Files':file_menu=True
            elif name=='Edit':edit_menu=True
            elif name=='Game Mode':
                mode='play' if mode=='edit' else 'edit'
                if mode=='play':
                    _rpg_scheduled.clear()
                    editor_start_events()
                if mode=='play' and dialogue_lines:
                    dialogue_index=0
                    dialogue=dialogue_lines[0]
                    dialogue_started_ms=pygame.time.get_ticks()
                input_active=False
                message='Mode: '+mode
            elif name=='Animation':active_panel='Animation'
            elif name=='Entities':entity_drawer_open=not entity_drawer_open
            return
    if entity_quick_menu and quick_menu_click(pos):return
    if entity_drawer_open and mode=='edit' and entity_drawer_click(pos):return
    side=layout()[1]
    for name,r in sidebar_buttons(side):
        if r.collidepoint(pos):sidebar_click(name);return
    if active_panel=='Scenes':
        for key,r in scene_ui_rects(side).items():
            if not r.collidepoint(pos):continue
            if key=='new':
                scene_name_input=True;scene_name_buffer='';input_active=True
                message='Type in the scene name popup and press Enter.'
            elif key=='next':
                ix=scene_order.index(current_scene)
                change_scene(scene_order[(ix+1)%len(scene_order)])
            elif key=='type':scene_cycle_type()
            elif key=='movement':
                movement_enabled=not movement_enabled
                message='Walking '+('ON' if movement_enabled else 'OFF')
            elif key=='player':
                i=get_selected()
                if i and i['kind']=='character':
                    player_id=i['id'];message='Player set to '+i['name']
                else:message='Select a character on canvas first.'
            elif key=='dialogue':
                active_panel='Code Chat';input_active=True
                message='Type SAY Your dialogue here, then Enter.'
            elif key=='choice':
                active_panel='Code Chat';input_active=True
                message='Type CHOICE Help her -> Scene 2, then Enter.'
            elif key=='collision':
                collision_enabled=not collision_enabled
                message='Collision '+('ON' if collision_enabled else 'OFF')
            elif key=='timeline':
                active_panel='Code Chat';input_active=True
                message='Type LINE Character 1: Hello! to add a dialogue line.'
            return
    if active_panel=='Story Flow':
        area,nodes=story_flow_nodes(side)
        if area.collidepoint(pos):
            for name,rect in nodes.items():
                if rect.collidepoint(pos):change_scene(name);return
        return
    if active_panel=='Python Script':
        for name,r in panel_buttons(side):
            if r.collidepoint(pos):
                if name=='Run Python':run_python_script()
                elif name=='Clear':
                    script_lines[:]=[''];script_row=script_col=0;script_confirm=False
                return
        if script_editor_rect(side).collidepoint(pos):
            input_active=True
            area=script_editor_rect(side)
            script_row=max(0,min(len(script_lines)-1,script_scroll+(pos[1]-area.y-6)//19))
            script_col=len(script_lines[script_row])
            return
    elif active_panel=='Coding Manual':
        for name,r in panel_buttons(side):
            if r.collidepoint(pos):
                if name=='Edit Manual File':
                    open_manual_file()
                elif name=='Reload':load_manual()
                return
    if active_panel=='Code Chat':
        entry=pygame.Rect(side.x+12,side.bottom-55,side.w-110,33)
        if entry.collidepoint(pos):input_active=True;return
        if pygame.Rect(side.right-84,side.bottom-55,68,33).collidepoint(pos):
            run_input();return
    elif active_panel=='Edit Text':
        entry=pygame.Rect(side.x+12,side.y+410+80,side.w-24,38)
        if entry.collidepoint(pos):
            input_active=True
            return
    elif active_panel=='Animation':
        for name,r in panel_buttons(side):
            if r.collidepoint(pos):
                if name=='New Animation':
                    animation_name_input=True;animation_name_buffer=''
                elif name=='Add Frame':add_animation_frame()
                elif name=='Next Animation':
                    item=get_selected()
                    if item and item.get('animations'):
                        names=list(item['animations']);idx=names.index(item['active_animation']) if item.get('active_animation') in names else -1
                        item['active_animation']=names[(idx+1)%len(names)];message='Selected '+item['active_animation']
                else:
                    item=get_selected()
                    if item and selected_animation(item):
                        if item.get('_anim_playing'):item['_anim_playing']=False;message='Animation paused'
                        else:play_named_animation(item,item['active_animation']);message='Animation playing'
                return
    elif active_panel=='Sounds':
        for name,r in panel_buttons(side):
            if r.collidepoint(pos):
                if name=='Import Sound':execute('SOUND IMPORT')
                elif name=='Play Sound':
                    if scene_sounds:play_sound(next(reversed(scene_sounds)))
                    else:message='Import a sound first'
                else:execute('SOUND STOP')
                return
    elif active_panel=='Overlays':
        for name,r in panel_buttons(side):
            if r.collidepoint(pos):
                if name=='New Overlay':
                    path=choose_file('Choose overlay PNG')
                    if path:
                        new_item('object',path)
                        owner=get_selected()
                        if owner:owner['overlay']=True;owner['name']='Overlay '+str(owner['id'])
                elif name=='Set Parent (Code Chat)':
                    message='Select CHILD, then in Code Chat type PARENT Character 1 (or another item name)'
                else:
                    owner=get_selected()
                    if owner:detach_item(owner);message='Detached '+owner['name']
                return
    elif active_panel=='Background Size':
        for name,r in panel_buttons(side):
            if r.collidepoint(pos):
                if name=='Width -':bg_rect[2]=max(20,bg_rect[2]-20)
                elif name=='Width +':bg_rect[2]+=20
                elif name=='Height -':bg_rect[3]=max(20,bg_rect[3]-20)
                else:bg_rect[3]+=20
                return
    if animation_name_input:return
    if mode=='play':
        canvas=canvas_rect()
        if dialogue and scene_type!='choice':
            dialogue_box=pygame.Rect(canvas.x+20,canvas.bottom-int(dialogue_style.get('height',90))-22,canvas.w-40,int(dialogue_style.get('height',90)))
            if dialogue_box.collidepoint(pos):
                advance_dialogue()
                return
        if scene_type=='choice' and scene_choices:
            cr_area=pygame.Rect(canvas.x+max(10,canvas.w//5),canvas.y+max(40,canvas.h//4),
                                max(170,canvas.w*3//5),min(canvas.h-90,80+len(scene_choices)*47))
            for j,ch in enumerate(scene_choices[:7]):
                cr=pygame.Rect(cr_area.x+15,cr_area.y+45+j*47,cr_area.w-30,39)
                if cr.collidepoint(pos):activate_choice(j);return

        for i in reversed(items):
            if i.get('visible',True) and rect_for(i,canvas).collidepoint(pos):
                editor_event(i,'click')
                if i['kind']=='button' and not i.get('events',{}).get('click'):dialogue='Clicked '+i['text']
                return
        editor_event({'events':scene_events},'click')
        return
    input_active=False
    canvas=canvas_rect()
    if not canvas.collidepoint(pos):return
    if pygame.key.get_pressed()[pygame.K_SPACE]:
        global map_panning,map_pan_start,map_pan_origin
        map_panning=True;map_pan_start=pos;map_pan_origin=tuple(editor_pan)
        return
    for i in reversed(items):
        r=rect_for(i,canvas)
        if not i.get('visible',True):continue
        if r.collidepoint(pos):
            selected_id=i['id'];dragging=True
            resize_drag=pygame.Rect(r.right-14,r.bottom-14,22,22).collidepoint(pos)
            drag_offset=(pos[0]-r.x,pos[1]-r.y)
            return
    selected_id=None

def input_text_placeholder():
    global input_text
    i=get_selected();input_text=i['text'] if i else ''

def run_input():
    global input_text,input_active,message
    if active_panel=='Edit Text':
        i=get_selected()
        if i and i['kind'] in ('text','button'):i['text']=input_text;message='Text updated'
        active_to_chat();return
    if not input_text.strip():
        message='Type a command first. Try HELP.'
        return
    execute(input_text);input_text='';input_active=True

def active_to_chat():
    global active_panel,input_active,input_text
    active_panel='Code Chat';input_active=False;input_text=''

# V61: gentle, non-blocking navigation transitions and a startup splash.
# Page changes are observed centrally, so existing click handlers remain unchanged.
_v61_page_key = None
_v61_transition_start = None
_v61_transition_ms = 290
_v61_splash_start = None
_v61_splash_ms = 1150

def v61_page_key():
    if ONLINE_AUTH_SCREEN:
        return ('auth',)
    if app_page == 'editor':
        return ('editor', mode, project_name)
    if app_page == 'community':
        return ('community', community_state.get('page', 'gallery'))
    return ('home',)

def v61_paint_splash(now):
    global _v61_splash_start
    if _v61_splash_start is None:
        _v61_splash_start = now
    elapsed = now - _v61_splash_start
    if elapsed >= _v61_splash_ms:
        return False
    w, h = screen.get_size()
    screen.fill(COL['bg'])
    cx, cy = w // 2, h // 2
    font_title = pygame.font.SysFont('Arial', 43, bold=True)
    font_sub = pygame.font.SysFont('Arial', 19)
    title = font_title.render('2d appmaker  ♥', True, COL['accent'])
    screen.blit(title, title.get_rect(center=(cx, cy-64)))
    subtitle = font_sub.render('Your cozy little game studio', True, COL['muted'])
    screen.blit(subtitle, subtitle.get_rect(center=(cx, cy-15)))
    track = pygame.Rect(cx-155, cy+40, 310, 9)
    pygame.draw.rect(screen, COL['panel'], track, border_radius=5)
    progress = max(0.0, min(1.0, elapsed / _v61_splash_ms))
    # Smoothly animated progress is a visual introduction, not a measurement of disk I/O.
    fill = track.copy(); fill.w = max(1, round(track.w*progress))
    pygame.draw.rect(screen, COL['accent'], fill, border_radius=5)
    pygame.display.flip()
    return True

def v61_paint_transition(now):
    global _v61_page_key, _v61_transition_start
    key = v61_page_key()
    if _v61_page_key is None:
        _v61_page_key = key
        return
    if key != _v61_page_key:
        _v61_page_key = key
        _v61_transition_start = now
    if _v61_transition_start is None:
        return
    elapsed = now - _v61_transition_start
    if elapsed >= _v61_transition_ms:
        _v61_transition_start = None
        return
    # Fade a dark veil away from the newly rendered page; input stays responsive.
    fraction = 1.0 - elapsed / _v61_transition_ms
    opacity = max(0, min(180, round(180 * fraction * fraction)))
    veil = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    veil.fill((*COL['bg'], opacity))
    screen.blit(veil, (0, 0))
    pygame.display.flip()

async def main():
    global _rpg_editor_last_second, _v63_delete_hold_start, a, after, animation_name_buffer, animation_name_input, app_page, bar, before, branch_scroll, canvas, destination, dialogue, dragging, dx, dy, editor_pan, editor_zoom, entity_code_scroll, entity_drawer_dragging, entity_drawer_pos, entity_folder_buffer, entity_folder_input, entity_rename_buffer, entity_rename_open, entity_row_drag, entity_scroll, event, extra, frame_index, held, home_scroll, i, indent, input_active, input_text, keyname, keys, kind, line, manual_hdrag, manual_scroll, manual_scroll_x, map_pan_origin, map_pan_start, map_panning, maximum, message, mode, mx, my, name, new_name, now, nx, ny, o, obj, parent, player, prev, px, py, r, resize_drag, running, scene_name_buffer, scene_name_input, screen, script_col, script_confirm, script_row, script_scroll, second, selected_id, sx, sy, target, value, visible, world_x, world_y, wx, wy
    if sys.platform != 'emscripten':
        online_restore_session()
    running=True
    while running:
        for event in pygame.event.get():
            if event.type==pygame.QUIT:running=False
            elif event.type==pygame.VIDEORESIZE:
                screen=pygame.display.set_mode((max(1000,event.w),max(860,event.h)),pygame.RESIZABLE)
            elif ONLINE_AUTH_SCREEN:
                if event.type==pygame.MOUSEBUTTONDOWN and event.button==1:online_auth_click(event.pos)
                elif event.type==pygame.KEYDOWN:online_auth_key(event)
            elif app_page=='community':
                if event.type==pygame.MOUSEBUTTONDOWN and event.button==1:
                    if community_click(event.pos,bundled_player_path())=='home':app_page='home'
                elif event.type==pygame.KEYDOWN:community_key(event)
                elif event.type==pygame.MOUSEWHEEL:community_state['scroll']=max(0,community_state['scroll']-event.y)
            elif app_page=='home':
                if event.type==pygame.MOUSEBUTTONDOWN and event.button==1:home_click(event.pos)
                elif event.type==pygame.KEYDOWN:home_key(event)
                elif event.type==pygame.MOUSEWHEEL:home_scroll=max(0,min(max(0,(len(project_list())-1)//gallery_layout()[2]-gallery_layout()[-1]+1),home_scroll-event.y))
            elif event.type==pygame.MOUSEBUTTONDOWN and event.button==1:
                if entity_code_open:entity_code_click(event.pos)
                else:click(event.pos)
            elif event.type==pygame.MOUSEBUTTONDOWN and event.button==2 and mode=='edit' and canvas_rect().collidepoint(event.pos):
                map_panning=True;map_pan_start=event.pos;map_pan_origin=tuple(editor_pan)
            elif event.type==pygame.MOUSEBUTTONUP and event.button==2:
                map_panning=False
            elif event.type==pygame.MOUSEWHEEL and mode=='edit' and canvas_rect().collidepoint(pygame.mouse.get_pos()) and not entity_code_open and not (entity_drawer_open and entity_drawer_rect().collidepoint(pygame.mouse.get_pos())):
                canvas=canvas_rect();mx,my=pygame.mouse.get_pos()
                wx,wy=screen_to_world(mx,my,canvas)
                editor_zoom=max(0.15,min(3.0,editor_zoom*(1.12**event.y)))
                editor_pan[0]=(mx-canvas.x)/editor_zoom-wx
                editor_pan[1]=(my-canvas.y)/editor_zoom-wy
            elif event.type==pygame.MOUSEWHEEL and entity_code_open:
                entity_code_scroll=max(0,min(max(0,len(entity_code_lines)-3),entity_code_scroll-event.y))
            elif event.type==pygame.MOUSEWHEEL and entity_drawer_open and entity_drawer_rect().collidepoint(pygame.mouse.get_pos()):
                entity_scroll=max(0,min(max(0,len(entity_folder_entries())-entity_visible_capacity()),entity_scroll-event.y))
            elif event.type==pygame.MOUSEWHEEL and active_panel=='Story Flow':
                branch_scroll=max(0,min(max(0,len(scene_order)-2),branch_scroll-event.y))
            elif event.type==pygame.MOUSEWHEEL and active_panel=='Coding Manual':
                if pygame.key.get_mods() & pygame.KMOD_SHIFT or event.x:
                    manual_scroll_x=max(0,min(manual_max_scroll_x(layout()[1]),manual_scroll_x-(event.x if event.x else event.y)*38))
                else:
                    manual_scroll=max(0,min(max(0,len(MANUAL)-9),manual_scroll-event.y*3))
            elif event.type==pygame.MOUSEWHEEL and active_panel=='Python Script':
                script_scroll=max(0,min(max(0,len(script_lines)-2),script_scroll-event.y*2))
            elif event.type==pygame.KEYUP and app_page=='editor' and mode=='play' and not input_active:
                keyname=pygame.key.name(event.key).lower()
                editor_event({'events':scene_events},'key:'+keyname+':released')
                for obj in list(items):editor_event(obj,'key:'+keyname+':released')
            elif event.type==pygame.MOUSEMOTION and map_panning:
                editor_pan[0]=map_pan_origin[0]+(event.pos[0]-map_pan_start[0])/editor_zoom
                editor_pan[1]=map_pan_origin[1]+(event.pos[1]-map_pan_start[1])/editor_zoom
            elif event.type==pygame.MOUSEMOTION and manual_hdrag:
                bar=manual_scrollbar_rect(layout()[1],layout()[1].y+410)
                maximum=manual_max_scroll_x(layout()[1])
                manual_scroll_x=max(0,min(maximum,int((event.pos[0]-bar.x)/max(1,bar.w)*maximum)))
            elif event.type==pygame.MOUSEBUTTONUP and event.button==1:
                if entity_row_drag and entity_row_drag.get('active'):
                    destination=next((value for kind,value,r in entity_sound_rows() if kind=='folder' and r.collidepoint(event.pos)),None)
                    if destination is not None:
                        if entity_row_drag['kind']=='entity':entity_assignments[str(entity_row_drag['value'])]=destination
                        else:audio_assignments[entity_row_drag['value']]=destination
                        entity_folder_closed.discard(destination)
                        message='Moved into folder: '+destination
                    else:
                        message='Drop an item onto a folder row to move it'
                entity_row_drag=None
                dragging=False;resize_drag=False;entity_drawer_dragging=False;manual_hdrag=False;map_panning=False
            elif event.type==pygame.MOUSEMOTION and entity_row_drag:
                sx,sy=entity_row_drag['start']
                if (event.pos[0]-sx)**2+(event.pos[1]-sy)**2>64:
                    entity_row_drag['active']=True
            elif event.type==pygame.MOUSEMOTION and entity_drawer_dragging:
                entity_drawer_pos=[event.pos[0]-entity_drawer_drag_offset[0],event.pos[1]-entity_drawer_drag_offset[1]]
                entity_drawer_rect()  # clamp to visible screen
            elif event.type==pygame.MOUSEMOTION and dragging and mode=='edit':
                i=get_selected()
                if i:
                    canvas=canvas_rect()
                    if resize_drag:
                        world_x,world_y=item_world_position(i)
                        mx,my=screen_to_world(*event.pos,canvas)
                        i['w']=max(20,round(mx-world_x))
                        i['h']=max(20,round(my-world_y))
                    else:
                        nx,ny=screen_to_world(event.pos[0]-drag_offset[0]+canvas.x,event.pos[1]-drag_offset[1]+canvas.y,canvas)
                        nx,ny=round(nx),round(ny)
                        parent=next((a for a in items if a['id']==i.get('parent_id')),None)
                        if parent:
                            px,py=item_world_position(parent)
                            nx-=px;ny-=py
                        i['x']=nx;i['y']=ny
            elif event.type==pygame.KEYDOWN:
                if mode=='edit' and event.key==pygame.K_0 and pygame.key.get_mods() & pygame.KMOD_CTRL:
                    editor_zoom=1.0;editor_pan=[0.0,0.0]
                    continue
                if entity_drawer_open and not entity_code_open and not entity_folder_input and not entity_rename_open and entity_drawer_rect().collidepoint(pygame.mouse.get_pos()) and event.key in (pygame.K_PAGEUP,pygame.K_PAGEDOWN):
                    entity_scroll=max(0,min(max(0,len(entity_folder_entries())-entity_visible_capacity()),entity_scroll+(-4 if event.key==pygame.K_PAGEUP else 4)))
                    continue
                if entity_code_open and app_page=='editor':
                    entity_code_key(event)
                    continue
                if app_page=='editor' and mode=='play' and not input_active and not scene_name_input and not animation_name_input and not entity_rename_open:
                    keyname=pygame.key.name(event.key).lower()
                    editor_event({'events':scene_events},'key:'+keyname+':pressed')
                    for obj in list(items):editor_event(obj,'key:'+keyname+':pressed')
                if entity_folder_input:
                    if event.key==pygame.K_RETURN:
                        name=entity_folder_buffer.strip()
                        if name and name not in entity_folders:
                            entity_folders.append(name)
                            message='Folder created: '+name+' · select an item and click Move to Folder'
                        elif name:message='Folder already exists'
                        entity_folder_input=False
                    elif event.key==pygame.K_ESCAPE:entity_folder_input=False
                    elif event.key==pygame.K_BACKSPACE:entity_folder_buffer=entity_folder_buffer[:-1]
                    elif event.unicode and event.unicode.isprintable() and len(entity_folder_buffer)<25:entity_folder_buffer+=event.unicode
                elif entity_rename_open:
                    if event.key==pygame.K_RETURN:
                        target=get_selected();new_name=entity_rename_buffer.strip()
                        if target and new_name:
                            if any(o['id']!=target['id'] and o['name'].lower()==new_name.lower() for o in items):message='Name already in use'
                            else:target['name']=new_name;entity_rename_open=False;message='Renamed to '+new_name
                        else:entity_rename_open=False
                    elif event.key==pygame.K_ESCAPE:entity_rename_open=False
                    elif event.key==pygame.K_BACKSPACE:entity_rename_buffer=entity_rename_buffer[:-1]
                    elif event.unicode.isprintable() and len(entity_rename_buffer)<35:entity_rename_buffer+=event.unicode
                elif animation_name_input:
                    if event.key==pygame.K_RETURN:
                        create_animation_clip(animation_name_buffer)
                        animation_name_input=False;animation_name_buffer=''
                    elif event.key==pygame.K_ESCAPE:animation_name_input=False;animation_name_buffer=''
                    elif event.key==pygame.K_BACKSPACE:animation_name_buffer=animation_name_buffer[:-1]
                    elif event.unicode.isprintable() and len(animation_name_buffer)<28:animation_name_buffer+=event.unicode
                elif scene_name_input:
                    if event.key==pygame.K_RETURN:
                        add_scene(scene_name_buffer)
                        scene_name_input=False;input_active=False;scene_name_buffer=''
                    elif event.key==pygame.K_ESCAPE:
                        scene_name_input=False;input_active=False
                    elif event.key==pygame.K_BACKSPACE:scene_name_buffer=scene_name_buffer[:-1]
                    elif event.unicode.isprintable() and len(scene_name_buffer)<28:
                        scene_name_buffer+=event.unicode
                elif mode=='play' and movement_enabled and scene_type=='exploration' and not input_active:
                    player=next((i for i in items if i['id']==player_id and i['kind']=='character'),None)
                    if player is None:player=next((i for i in items if i['kind']=='character'),None)
                    if player:
                        keys={pygame.K_w:(0,-12),pygame.K_UP:(0,-12),pygame.K_s:(0,12),pygame.K_DOWN:(0,12),
                              pygame.K_a:(-12,0),pygame.K_LEFT:(-12,0),pygame.K_d:(12,0),pygame.K_RIGHT:(12,0)}
                        if event.key in keys:
                            dx,dy=keys[event.key]
                            if movement_allowed(player,player['x']+dx,player['y']):player['x']+=dx
                            if movement_allowed(player,player['x'],player['y']+dy):player['y']+=dy
                elif mode=='play' and event.key in (pygame.K_SPACE,pygame.K_RETURN) and not input_active:
                    advance_dialogue()
                elif input_active and active_panel=='Python Script':
                    script_confirm=False
                    line=script_lines[script_row]
                    if event.key==pygame.K_RETURN:
                        before,after=line[:script_col],line[script_col:]
                        indent=len(before)-len(before.lstrip(' '))
                        extra=4 if before.rstrip().endswith(':') else 0
                        script_lines[script_row]=before
                        script_lines.insert(script_row+1,' '*(indent+extra)+after)
                        script_row+=1;script_col=indent+extra
                    elif event.key==pygame.K_BACKSPACE:
                        if script_col>0:
                            script_lines[script_row]=line[:script_col-1]+line[script_col:]
                            script_col-=1
                        elif script_row>0:
                            prev=script_lines[script_row-1]
                            script_col=len(prev)
                            script_lines[script_row-1]=prev+line
                            script_lines.pop(script_row);script_row-=1
                    elif event.key==pygame.K_TAB:
                        script_lines[script_row]=line[:script_col]+'    '+line[script_col:];script_col+=4
                    elif event.key==pygame.K_LEFT:
                        script_col=max(0,script_col-1)
                    elif event.key==pygame.K_RIGHT:
                        script_col=min(len(line),script_col+1)
                    elif event.key==pygame.K_UP:
                        script_row=max(0,script_row-1);script_col=min(script_col,len(script_lines[script_row]))
                    elif event.key==pygame.K_DOWN:
                        script_row=min(len(script_lines)-1,script_row+1);script_col=min(script_col,len(script_lines[script_row]))
                    elif event.key==pygame.K_ESCAPE:input_active=False
                    elif event.unicode and event.unicode.isprintable():
                        script_lines[script_row]=line[:script_col]+event.unicode+line[script_col:]
                        script_col+=len(event.unicode)
                    visible=max(1,(script_editor_rect(layout()[1]).h-12)//19)
                    script_scroll=max(0,min(script_scroll,script_row)) if script_row<script_scroll else max(script_scroll,script_row-visible+1)
                elif input_active:
                    if event.key==pygame.K_RETURN:run_input()
                    elif event.key==pygame.K_ESCAPE:input_active=False
                    elif event.key==pygame.K_BACKSPACE:input_text=input_text[:-1]
                    elif event.unicode and event.unicode.isprintable():input_text+=event.unicode
                elif event.key in (pygame.K_DELETE,pygame.K_BACKSPACE) and mode=='edit':
                    i=get_selected()
                    if i:items.remove(i);selected_id=None;message='Item deleted'
                elif event.key==pygame.K_ESCAPE:mode='edit';dialogue=''
        if app_page=='editor' and mode=='play':
            second=pygame.time.get_ticks()//1000
            if second!=_rpg_editor_last_second:
                _rpg_editor_last_second=second
                editor_event({'events':scene_events},'tick')
                for obj in list(items):editor_event(obj,'tick')
        if app_page=='editor' and mode=='play':
            v58_touch_events()
            process_rpg_schedule()
        # Hold Delete for 1.2 seconds in Edit Mode to clear all entities.
        if app_page=='editor' and mode=='edit' and not input_active:
            held=pygame.key.get_pressed()[pygame.K_DELETE]
            if held:
                if globals().get('_v63_delete_hold_start') is None:
                    _v63_delete_hold_start=pygame.time.get_ticks()
                elif pygame.time.get_ticks()-_v63_delete_hold_start>=1200:
                    items.clear();selected_id=None
                    message='All scene entities cleared (long-hold Delete).'
                    _v63_delete_hold_start=pygame.time.get_ticks()+1000000
            else:
                _v63_delete_hold_start=None
        else:
            _v63_delete_hold_start=None
        frame_index+=1
        now = pygame.time.get_ticks()
        if not v61_paint_splash(now):
            draw()
            v61_paint_transition(now)
        clock.tick(60)
        await asyncio.sleep(0)
    if app_page=='editor':save_current_project()
    pygame.quit()


if __name__ == "__main__":
    asyncio.run(main())
