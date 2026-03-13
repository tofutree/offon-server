from flask import Flask, request, jsonify
from flask_cors import CORS
from twilio.twiml.messaging_response import MessagingResponse
import firebase_admin
from firebase_admin import credentials, firestore
import os
import json
from datetime import datetime

app = Flask(__name__)
CORS(app)

# Firebase 초기화 (환경변수에서 읽기)
firebase_key = os.environ.get('FIREBASE_KEY')
if firebase_key:
    key_dict = json.loads(firebase_key)
    cred = credentials.Certificate(key_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
else:
    db = None
    print("WARNING: FIREBASE_KEY not set")

OFFON_NUMBER = os.environ.get('OFFON_NUMBER', '+18448860777')

@app.route('/', methods=['GET'])
def index():
    return 'off/on server is running 📡'

@app.route('/mms', methods=['POST'])
def receive_mms():
    from_number = request.form.get('From', '')
    body = request.form.get('Body', '')
    num_media = int(request.form.get('NumMedia', 0))

    media_url = None
    media_type = None
    if num_media > 0:
        media_url = request.form.get('MediaUrl0', '')
        media_type = request.form.get('MediaContentType0', '')

    if not db:
        return 'server error', 500

    user_query = db.collection('users').where('phone', '==', from_number).limit(1).get()

    if not user_query:
        resp = MessagingResponse()
        resp.message("Hey! You're not registered yet. Sign up at offon.com 📡")
        return str(resp)

    user_doc = user_query[0]
    user_data = user_doc.to_dict()

    post = {
        'user_id': user_doc.id,
        'username': user_data.get('username', 'anonymous'),
        'handle': user_data.get('handle', '@unknown'),
        'is_public': user_data.get('is_public', True),
        'text': body,
        'media_url': media_url,
        'media_type': media_type,
        'device': user_data.get('device', 'phone'),
        'created_at': datetime.utcnow(),
        'likes': 0,
        'source': 'mms'
    }

    db.collection('posts').add(post)
    user_doc.reference.update({
        'post_count': firestore.Increment(1),
        'last_posted': datetime.utcnow()
    })

    resp = MessagingResponse()
    resp.message("✓ posted to off/on")
    return str(resp)


@app.route('/api/posts', methods=['GET'])
def get_posts():
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    limit = int(request.args.get('limit', 20))
    handle = request.args.get('handle', None)
    posts_ref = db.collection('posts')

    if handle:
        query = posts_ref.where('handle', '==', handle).order_by('created_at', direction=firestore.Query.DESCENDING).limit(limit)
    else:
        query = posts_ref.where('is_public', '==', True).order_by('created_at', direction=firestore.Query.DESCENDING).limit(limit)

    posts = []
    for doc in query.get():
        post = doc.to_dict()
        post['id'] = doc.id
        post['created_at'] = post['created_at'].isoformat() if post.get('created_at') else None
        posts.append(post)

    return jsonify({'posts': posts})


@app.route('/api/register', methods=['POST'])
def register():
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    data = request.json
    phone = data.get('phone')
    username = data.get('username')
    handle = data.get('handle')
    is_public = data.get('is_public', True)
    device = data.get('device', 'phone')

    if not phone or not username or not handle:
        return jsonify({'error': 'phone, username, handle required'}), 400

    existing = db.collection('users').where('handle', '==', handle).limit(1).get()
    if existing:
        return jsonify({'error': 'handle already taken'}), 409

    user = {
        'phone': phone,
        'username': username,
        'handle': handle,
        'is_public': is_public,
        'device': device,
        'post_count': 0,
        'created_at': datetime.utcnow()
    }

    doc_ref = db.collection('users').add(user)
    return jsonify({
        'success': True,
        'user_id': doc_ref[1].id,
        'message': f'Welcome to off/on, {username}! Text {OFFON_NUMBER} to post.'
    })


@app.route('/api/user/<handle>', methods=['GET'])
def get_user(handle):
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    users = db.collection('users').where('handle', '==', handle).limit(1).get()
    if not users:
        return jsonify({'error': 'user not found'}), 404

    user = users[0].to_dict()
    user.pop('phone', None)
    user['created_at'] = user['created_at'].isoformat() if user.get('created_at') else None
    if user.get('last_posted'):
        user['last_posted'] = user['last_posted'].isoformat()

    return jsonify(user)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
