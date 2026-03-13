# off/on server
# MMS 수신 → Firebase 저장 → 피드 표시

from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
import firebase_admin
from firebase_admin import credentials, firestore
import os
from datetime import datetime
import requests

app = Flask(__name__)

# Firebase 초기화
cred = credentials.Certificate('firebase-key.json')
firebase_admin.initialize_app(cred)
db = firestore.client()

# Twilio 설정
TWILIO_ACCOUNT_SID = os.environ.get('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.environ.get('TWILIO_AUTH_TOKEN')
OFFON_NUMBER = os.environ.get('OFFON_NUMBER', '+15707077010')

@app.route('/', methods=['GET'])
def index():
    return 'off/on server is running 📡'

@app.route('/mms', methods=['POST'])
def receive_mms():
    """Twilio가 MMS를 받으면 이 엔드포인트로 전달함"""
    
    from_number = request.form.get('From', '')
    body = request.form.get('Body', '')
    num_media = int(request.form.get('NumMedia', 0))
    
    # 이미지 URL 추출
    media_url = None
    media_type = None
    if num_media > 0:
        media_url = request.form.get('MediaUrl0', '')
        media_type = request.form.get('MediaContentType0', '')
    
    # 전화번호로 사용자 찾기
    users_ref = db.collection('users')
    user_query = users_ref.where('phone', '==', from_number).limit(1).get()
    
    if not user_query:
        # 등록된 사용자가 아닌 경우
        resp = MessagingResponse()
        resp.message("Hey! You're not registered yet. Sign up at offon.com to start posting 📡")
        return str(resp)
    
    user_doc = user_query[0]
    user_data = user_doc.to_dict()
    user_id = user_doc.id
    
    # 포스트 저장
    post = {
        'user_id': user_id,
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
    
    # 사용자 포스트 카운트 업데이트
    user_doc.reference.update({
        'post_count': firestore.Increment(1),
        'last_posted': datetime.utcnow()
    })
    
    # 자동 답장 (선택사항)
    resp = MessagingResponse()
    resp.message("✓ posted to off/on")
    return str(resp)


@app.route('/api/posts', methods=['GET'])
def get_posts():
    """피드용 포스트 가져오기"""
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
    """새 사용자 등록"""
    data = request.json
    
    phone = data.get('phone')
    username = data.get('username')
    handle = data.get('handle')
    is_public = data.get('is_public', True)
    device = data.get('device', 'phone')
    
    if not phone or not username or not handle:
        return jsonify({'error': 'phone, username, handle required'}), 400
    
    # 중복 체크
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
        'message': f'Welcome to off/on, {username}! Text +15707077010 to post.'
    })


@app.route('/api/user/<handle>', methods=['GET'])
def get_user(handle):
    """사용자 프로필"""
    users = db.collection('users').where('handle', '==', handle).limit(1).get()
    
    if not users:
        return jsonify({'error': 'user not found'}), 404
    
    user = users[0].to_dict()
    user.pop('phone', None)  # 전화번호는 숨김
    user['created_at'] = user['created_at'].isoformat() if user.get('created_at') else None
    user['last_posted'] = user['last_posted'].isoformat() if user.get('last_posted') else None
    
    return jsonify(user)


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
