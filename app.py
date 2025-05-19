# app.py
from flask import Flask, request, jsonify, send_from_directory, render_template, redirect, url_for, session
from yt_dlp import YoutubeDL
import os
import uuid
import re
import shutil
from urllib.parse import urlparse, parse_qs
import tempfile
import threading
import time
import json
import math

app = Flask(__name__, static_folder='static')
app.secret_key = os.environ.get('SECRET_KEY', 'une_cle_secrete_aleatoire')

# Configuration
DOWNLOAD_FOLDER = os.environ.get('DOWNLOAD_FOLDER', tempfile.mkdtemp())
app.config['DOWNLOAD_FOLDER'] = DOWNLOAD_FOLDER
MAX_AGE_HOURS = 1  # Les fichiers seront supprimés après 1 heure
CLEANUP_INTERVAL = 600  # Nettoyer toutes les 10 minutes

# Créer le dossier si nécessaire
if not os.path.exists(DOWNLOAD_FOLDER):
    os.makedirs(DOWNLOAD_FOLDER)

def clean_old_files():
    """Nettoyer périodiquement les fichiers temporaires"""
    while True:
        try:
            now = time.time()
            for filename in os.listdir(DOWNLOAD_FOLDER):
                file_path = os.path.join(DOWNLOAD_FOLDER, filename)
                # Supprimer les fichiers de plus d'une heure
                if os.path.isfile(file_path) and now - os.path.getmtime(file_path) > MAX_AGE_HOURS * 3600:
                    os.remove(file_path)
                elif os.path.isdir(file_path) and now - os.path.getmtime(file_path) > MAX_AGE_HOURS * 3600:
                    shutil.rmtree(file_path)
        except Exception as e:
            print(f"Erreur lors du nettoyage: {e}")
        
        time.sleep(CLEANUP_INTERVAL)

# Démarrer le thread de nettoyage
cleanup_thread = threading.Thread(target=clean_old_files, daemon=True)
cleanup_thread.start()

def extract_video_id(url):
    """Extrait l'ID de la vidéo YouTube à partir de l'URL"""
    # Format: https://www.youtube.com/watch?v=VIDEO_ID
    parsed_url = urlparse(url)
    if parsed_url.netloc in ('www.youtube.com', 'youtube.com'):
        if parsed_url.path == '/watch':
            return parse_qs(parsed_url.query).get('v', [None])[0]
    # Format: https://youtu.be/VIDEO_ID
    elif parsed_url.netloc == 'youtu.be':
        # Extraire uniquement l'ID de la vidéo (11 caractères) du chemin
        video_id = parsed_url.path[1:].split('?')[0]
        return video_id
    # Format: https://www.youtube.com/shorts/VIDEO_ID
    elif parsed_url.netloc in ('www.youtube.com', 'youtube.com') and '/shorts/' in parsed_url.path:
        video_id = parsed_url.path.split('/shorts/')[1].split('?')[0]
        return video_id
    # Format: https://www.youtube.com/embed/VIDEO_ID
    elif parsed_url.netloc in ('www.youtube.com', 'youtube.com') and '/embed/' in parsed_url.path:
        video_id = parsed_url.path.split('/embed/')[1].split('?')[0]
        return video_id
    return None

def is_valid_youtube_url(url):
    """Vérifie si l'URL est une URL YouTube valide"""
    # Regex plus flexible pour accepter différents formats d'URL YouTube
    patterns = [
        r'^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[a-zA-Z0-9_-]{11}.*$',
        r'^(https?://)?(www\.)?youtube\.com/shorts/[a-zA-Z0-9_-]{11}.*$',
        r'^(https?://)?(www\.)?youtube\.com/embed/[a-zA-Z0-9_-]{11}.*$'
    ]
    
    for pattern in patterns:
        if re.match(pattern, url):
            return True
    
    return False

def format_size(bytes):
    """Convertit les octets en format lisible (Mo)"""
    if bytes is None:
        return "Inconnue"
    
    bytes = float(bytes)
    if bytes < 1024*1024:
        return f"{bytes/1024:.1f} Ko"
    else:
        return f"{bytes/(1024*1024):.1f} Mo"

def format_resolution(format_info):
    """Formate la résolution et les informations du format"""
    if not format_info:
        return "Inconnu"
    
    resolution = format_info.get('resolution', 'Inconnue')
    ext = format_info.get('ext', '')
    vcodec = format_info.get('vcodec', '')
    acodec = format_info.get('acodec', '')
    
    # Vérifier si c'est audio seulement
    if vcodec == 'none' and acodec != 'none':
        return f"Audio seulement ({ext})"
    # Vérifier si c'est vidéo seulement
    elif vcodec != 'none' and acodec == 'none':
        return f"{resolution} - Vidéo seulement ({ext})"
    # Vidéo et audio
    else:
        return f"{resolution} ({ext})"

def get_quality_label(format_info):
    """Génère un libellé descriptif pour la qualité"""
    if not format_info:
        return "Inconnu"
    
    resolution = format_info.get('resolution', '')
    fps = format_info.get('fps', '')
    ext = format_info.get('ext', '')
    vcodec = format_info.get('vcodec', '')
    acodec = format_info.get('acodec', '')
    
    # Format audio seulement
    if vcodec == 'none' and acodec != 'none':
        abr = format_info.get('abr', '')
        return f"Audio {abr}kbps ({ext})"
    
    # Format vidéo
    label = f"{resolution}"
    if fps:
        label += f" {fps}fps"
    
    # Ajouter le type de fichier
    label += f" ({ext})"
    
    # Indiquer si c'est vidéo sans audio
    if acodec == 'none':
        label += " - Sans audio"
        
    return label

def get_video_formats(url):
    """Récupère les formats disponibles pour la vidéo"""
    options = {
        'quiet': True,
        'no_warnings': True,
        'ignoreerrors': True,
        'nocheckcertificate': True,
        'geo_bypass': True,
        'format_sort': ['res', 'fps', 'codec', 'size', 'br', 'asr'],
        'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
    }
    
    with YoutubeDL(options) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
            if not info:
                return None
                
            # Catégories pour organiser les formats
            video_formats = []  # Vidéo avec audio
            video_only_formats = []  # Vidéo sans audio
            audio_formats = []  # Audio uniquement
            
            # Résolutions prioritaires à afficher (dans l'ordre décroissant)
            priority_resolutions = [2160, 1440, 1080, 720, 480, 360, 240, 144]
            
            # Filtrer les formats pour éliminer les doublons et formats mal formatés
            seen_qualities = set()
            
            for f in info.get('formats', []):
                # Ignorer les formats mhtml et autres formats inutiles
                if f.get('ext') in ['mhtml']:
                    continue
                    
                # Récupérer la taille du fichier
                filesize = f.get('filesize')
                if filesize is None:
                    filesize = f.get('filesize_approx')
                
                # Vérifier si le format a des informations utiles
                if filesize is None and not f.get('resolution'):
                    continue
                
                format_id = f.get('format_id', '')
                resolution = f.get('resolution', '')  # Par exemple: "1920x1080"
                ext = f.get('ext', '')
                vcodec = f.get('vcodec', '')
                acodec = f.get('acodec', '')
                height = f.get('height')
                
                # Clé unique pour éviter les doublons
                quality_key = f"{resolution}_{ext}_{vcodec}_{acodec}"
                
                if quality_key not in seen_qualities:
                    seen_qualities.add(quality_key)
                    
                    format_info = {
                        'format_id': format_id,
                        'quality': get_quality_label(f),
                        'resolution': resolution,
                        'ext': ext,
                        'filesize': format_size(filesize),
                        'filesize_raw': filesize,
                        'width': f.get('width'),
                        'height': height,
                        'fps': f.get('fps'),
                        'vcodec': vcodec,
                        'acodec': acodec,
                        'has_audio': acodec != 'none',
                        'has_video': vcodec != 'none'
                    }
                    
                    # Simplifier la résolution pour l'affichage
                    if height:
                        format_info['display_resolution'] = f"{height}p"
                    
                    # Classer selon le type de format
                    if vcodec != 'none' and acodec != 'none':
                        video_formats.append(format_info)
                    elif vcodec != 'none' and acodec == 'none':
                        video_only_formats.append(format_info)
                    elif vcodec == 'none' and acodec != 'none':
                        audio_formats.append(format_info)
            
            # Trier et filtrer les formats vidéo pour privilégier les résolutions standard
            selected_video_formats = []
            for res in priority_resolutions:
                # Chercher le meilleur format pour chaque résolution prioritaire
                matches = [f for f in video_formats if f.get('height') == res]
                if matches:
                    # Trier par taille pour prendre le meilleur rapport qualité/taille
                    matches.sort(key=lambda x: (-(x.get('filesize_raw') or 0)))
                    selected_video_formats.append(matches[0])
            
            # Si aucun format n'a été trouvé dans les résolutions prioritaires, utiliser ceux qu'on a
            if not selected_video_formats and video_formats:
                # Limiter à 5 formats maximum, triés par résolution
                video_formats.sort(key=lambda x: (-(x.get('height') or 0)))
                selected_video_formats = video_formats[:5]
            
            # Trier les formats vidéo uniquement
            video_only_formats.sort(key=lambda x: (-(x.get('height') or 0)))
            
            # Ajouter les formats vidéo uniquement aux formats sélectionnés
            # Utile pour les utilisateurs qui veulent télécharger séparément vidéo et audio
            for res in priority_resolutions:
                matches = [f for f in video_only_formats if f.get('height') == res]
                if matches:
                    # Prendre le meilleur format pour chaque résolution
                    matches.sort(key=lambda x: (-(x.get('filesize_raw') or 0)))
                    selected_video_formats.append(matches[0])
            
            # Limiter le nombre de formats dans chaque catégorie
            audio_formats.sort(key=lambda x: (-(x.get('filesize_raw') or 0)))
            audio_formats = audio_formats[:3]  # Garder les 3 meilleures qualités audio
            
            # Fusionner les formats en gardant l'ordre des catégories
            all_formats = []
            
            # Ajouter l'option "best" en haut de la liste avec une taille estimée
            best_filesize = "Variable"
            if selected_video_formats and selected_video_formats[0].get('filesize'):
                best_filesize = format_size(selected_video_formats[0].get('filesize_raw') * 1.1)  # +10% d'estimation
                
            all_formats.append({
                'format_id': 'best',
                'quality': 'Meilleure qualité (recommandé)',
                'resolution': 'Auto',
                'display_resolution': 'Auto',
                'ext': 'mp4',
                'filesize': best_filesize,
                'filesize_raw': selected_video_formats[0].get('filesize_raw') if selected_video_formats else None,
                'has_audio': True,
                'has_video': True,
                'type': 'video'
            })
            
            # Ajouter les vidéos avec audio
            for f in selected_video_formats:
                f['type'] = 'video'
                all_formats.append(f)
                
            # Ajouter les formats audio uniquement
            for f in audio_formats:
                f['type'] = 'audio'
                all_formats.append(f)
            
            return {
                'title': info.get('title', 'Vidéo sans titre'),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
                'formats': all_formats
            }
            
        except Exception as e:
            print(f"Erreur lors de l'extraction des formats: {e}")
            return None
@app.route('/', methods=['GET'])
def home():
    return render_template('index.html')

@app.route('/formats', methods=['POST'])
def get_formats():
    video_url = request.form.get('video_url')
    
    if not video_url:
        return render_template('index.html', error_message='URL non fournie')
    
    if not is_valid_youtube_url(video_url):
        return render_template('index.html', error_message='URL YouTube invalide')
    
    try:
        video_info = get_video_formats(video_url)
        if not video_info:
            return render_template('index.html', error_message='Impossible de récupérer les informations de la vidéo')
        
        # Stocker l'URL et les informations dans la session
        session['video_url'] = video_url
        session['video_info'] = video_info
        
        return render_template('formats.html', video=video_info)
    
    except Exception as e:
        return render_template('index.html', error_message=f'Erreur: {str(e)}')

@app.route('/api/get_formats', methods=['POST'])
def api_get_formats():
    data = request.get_json()
    video_url = data.get('url')
    
    if not video_url:
        return jsonify({'error': 'URL non fournie'}), 400
    
    if not is_valid_youtube_url(video_url):
        return jsonify({'error': 'URL YouTube invalide'}), 400
    
    try:
        video_info = get_video_formats(video_url)
        if not video_info:
            return jsonify({'error': 'Impossible de récupérer les informations de la vidéo'}), 500
        
        return jsonify({
            'success': True,
            'video_info': video_info
        }), 200
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download_selected', methods=['POST'])
def download_selected():
    format_id = request.form.get('format_id')
    
    if 'video_url' not in session or 'video_info' not in session:
        return redirect(url_for('home'))
    
    video_url = session['video_url']
    video_info = session['video_info']
    
    try:
        # Générer un ID unique pour ce téléchargement
        download_id = str(uuid.uuid4())
        download_path = os.path.join(app.config['DOWNLOAD_FOLDER'], download_id)
        os.makedirs(download_path, exist_ok=True)
        
        options = {
            'format': format_id,
            'outtmpl': os.path.join(download_path, '%(title)s.%(ext)s'),
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'nocheckcertificate': True,
            'geo_bypass': True,
            'format_sort': ['res', 'fps', 'codec', 'size', 'br', 'asr'],
            'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
            'merge_output_format': 'mp4',
        }
        
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(video_url, download=True)
            
            if info is None:
                return render_template('formats.html', video=video_info, error_message='Impossible de télécharger cette vidéo')
                
            # Trouver le fichier téléchargé
            downloaded_files = os.listdir(download_path)
            if not downloaded_files:
                return render_template('formats.html', video=video_info, error_message='Échec du téléchargement')
            
            file_name = downloaded_files[0]
            file_path = os.path.join(download_id, file_name)
            
            # Construire l'URL de téléchargement
            file_url = url_for('serve_video', file_path=file_path)
            
            # Obtenir des informations sur la vidéo téléchargée
            video_result = {
                'title': info.get('title', 'Vidéo sans titre'),
                'file_url': file_url,
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
                'format': next((f['quality'] for f in video_info['formats'] if f['format_id'] == format_id), "Format personnalisé")
            }
            
            return render_template('download.html', video=video_result)

    except Exception as e:
        return render_template('formats.html', video=video_info, error_message=f'Erreur: {str(e)}')

@app.route('/api/download', methods=['POST'])
def download_video_api():
    data = request.get_json()
    video_url = data.get('url')
    format_id = data.get('format_id', 'best')
    
    if not video_url:
        return jsonify({'error': 'URL non fournie'}), 400
    
    if not is_valid_youtube_url(video_url):
        return jsonify({'error': 'URL YouTube invalide'}), 400
    
    try:
        # Générer un ID unique pour ce téléchargement
        download_id = str(uuid.uuid4())
        download_path = os.path.join(app.config['DOWNLOAD_FOLDER'], download_id)
        os.makedirs(download_path, exist_ok=True)
        
        options = {
            'format': format_id,
            'outtmpl': os.path.join(download_path, '%(title)s.%(ext)s'),
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'nocheckcertificate': True,
            'geo_bypass': True,
            'format_sort': ['res', 'fps', 'codec', 'size', 'br', 'asr'],
            'extractor_args': {'youtube': {'player_client': ['android', 'web']}},
            'merge_output_format': 'mp4',
        }
        
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(video_url, download=True)
            
            if info is None:
                return jsonify({'error': 'Impossible de télécharger cette vidéo'}), 500
                
            # Trouver le fichier téléchargé
            downloaded_files = os.listdir(download_path)
            if not downloaded_files:
                return jsonify({'error': 'Échec du téléchargement'}), 500
            
            file_name = downloaded_files[0]
            file_path = os.path.join(download_id, file_name)
            
            # Construire l'URL de téléchargement
            file_url = url_for('serve_video', file_path=file_path, _external=True)
            
            return jsonify({
                'success': True,
                'title': info.get('title', 'Vidéo sans titre'),
                'file_url': file_url,
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader')
            }), 200

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/download', methods=['POST'])
def download_form():
    # Rediriger vers le nouvel endpoint pour afficher les formats
    return redirect(url_for('get_formats'), code=307)

@app.route('/videos/<path:file_path>')
def serve_video(file_path):
    """Envoie le fichier téléchargé à l'utilisateur"""
    # Extraire le chemin du dossier et le nom du fichier
    parts = file_path.split('/', 1)
    if len(parts) != 2:
        return "Fichier introuvable", 404
    
    folder_id, filename = parts
    download_path = os.path.join(app.config['DOWNLOAD_FOLDER'], folder_id)
    
    return send_from_directory(download_path, filename, as_attachment=True)

@app.route('/robots.txt')
def robots():
    return send_from_directory(app.static_folder, 'robots.txt')

@app.route('/sitemap.xml')
def sitemap():
    return send_from_directory(app.static_folder, 'sitemap.xml')

@app.route('/health')
def health_check():
    """Route de vérification de la santé pour Railway"""
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host="0.0.0.0", port=port)
