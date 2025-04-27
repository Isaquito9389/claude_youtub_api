# app.py
from flask import Flask, request, jsonify, send_from_directory, render_template, redirect, url_for
from yt_dlp import YoutubeDL
import os
import uuid
import re
import shutil
from urllib.parse import urlparse, parse_qs
import tempfile
import threading
import time

app = Flask(__name__, static_folder='static')

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
        return parsed_url.path[1:]
    return None

def is_valid_youtube_url(url):
    """Vérifie si l'URL est une URL YouTube valide"""
    pattern = r'^(https?://)?(www\.)?(youtube\.com/watch\?v=|youtu\.be/)[a-zA-Z0-9_-]{11}.*$'
    return bool(re.match(pattern, url))

@app.route('/', methods=['GET'])
def home():
    return render_template('index.html')

@app.route('/api/download', methods=['POST'])
def download_video_api():
    data = request.get_json()
    video_url = data.get('url')
    
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
            'format': 'best',
            'outtmpl': os.path.join(download_path, '%(title)s.%(ext)s'),
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'nocheckcertificate': True,
            'geo_bypass': True,
            'extractor_args': {'youtube': {'player_client': ['android']}},
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
    video_url = request.form.get('video_url')
    
    if not video_url:
        return render_template('index.html', error_message='URL non fournie')
    
    if not is_valid_youtube_url(video_url):
        return render_template('index.html', error_message='URL YouTube invalide')
    
    try:
        # Générer un ID unique pour ce téléchargement
        download_id = str(uuid.uuid4())
        download_path = os.path.join(app.config['DOWNLOAD_FOLDER'], download_id)
        os.makedirs(download_path, exist_ok=True)
        
        options = {
            'format': 'best',
            'outtmpl': os.path.join(download_path, '%(title)s.%(ext)s'),
            'noplaylist': True,
            'quiet': True,
            'no_warnings': True,
            'ignoreerrors': True,
            'nocheckcertificate': True,
            'geo_bypass': True,
            'extractor_args': {'youtube': {'player_client': ['android']}},
        }
        
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(video_url, download=True)
            
            if info is None:
                return render_template('index.html', error_message='Impossible de télécharger cette vidéo')
                
            # Trouver le fichier téléchargé
            downloaded_files = os.listdir(download_path)
            if not downloaded_files:
                return render_template('index.html', error_message='Échec du téléchargement')
            
            file_name = downloaded_files[0]
            file_path = os.path.join(download_id, file_name)
            
            # Obtenir des informations sur la vidéo
            video_info = {
                'title': info.get('title', 'Vidéo sans titre'),
                'file_url': url_for('serve_video', file_path=file_path),
                'thumbnail': info.get('thumbnail'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader')
            }
            
            return render_template('download.html', video=video_info)

    except Exception as e:
        return render_template('index.html', error_message=f'Erreur: {str(e)}')

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

@app.route('/health')
def health_check():
    """Route de vérification de la santé pour Railway"""
    return jsonify({"status": "ok"}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host="0.0.0.0", port=port)