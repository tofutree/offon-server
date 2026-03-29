from flask import Flask, request, jsonify
from flask_cors import CORS
from twilio.twiml.messaging_response import MessagingResponse
import firebase_admin
from firebase_admin import credentials, firestore
import os
import json
import random
import string
import hashlib
import boto3
from botocore.client import Config
from datetime import datetime

app = Flask(__name__)
CORS(app)

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
EMAIL_DOMAIN = 'mail.offon.app'

R2_ACCESS_KEY = os.environ.get('R2_ACCESS_KEY_ID', '')
R2_SECRET_KEY = os.environ.get('R2_SECRET_ACCESS_KEY', '')
R2_ENDPOINT = os.environ.get('R2_ENDPOINT_URL', '')
R2_BUCKET = os.environ.get('R2_BUCKET_NAME', 'offon')

R2_PUBLIC_URL = os.environ.get('R2_PUBLIC_URL', '')

def generate_email_code():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))

def hash_password(password):
    return hashlib.sha256(password.encode()).hexdigest()

def upload_image_to_r2(file_obj, filename, content_type):
    try:
        s3 = boto3.client(
            's3',
            endpoint_url=R2_ENDPOINT,
            aws_access_key_id=R2_ACCESS_KEY,
            aws_secret_access_key=R2_SECRET_KEY,
            config=Config(signature_version='s3v4'),
            region_name='auto'
        )
        key = f'posts/{datetime.utcnow().strftime("%Y%m%d%H%M%S")}_{filename}'
        s3.upload_fileobj(file_obj, R2_BUCKET, key, ExtraArgs={
            'ContentType': content_type
        })
        public_url = f'{R2_PUBLIC_URL}/{key}'
        return public_url
    except Exception as e:
        print("R2 upload error:", e)
        return None


@app.route('/', methods=['GET'])
def index():
    return 'off/on server is running 📡'

@app.route('/mms', methods=['POST'])
def receive_mms():
    from_number = request.form.get('From', '')
    body = request.form.get('Body', '').strip()
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

    # #profile 기능
    if '#profile' in body.lower() and media_url and media_type and media_type.startswith('image/'):
        user_doc.reference.update({'profile_photo': media_url})
        resp = MessagingResponse()
        resp.message("✓ profile photo updated on off/on")
        return str(resp)

    post = {
        'user_id': user_doc.id,
        'username': user_data.get('username', 'anonymous'),
        'handle': user_data.get('handle', 'unknown'),
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
    requester = request.args.get('requester', None)  # 요청하는 사람의 handle
    posts_ref = db.collection('posts')

    if handle:
        # 해당 유저가 private인지 확인
        user_query = db.collection('users').where('handle', '==', handle).limit(1).get()
        if user_query:
            user_data = user_query[0].to_dict()
            is_public = user_data.get('is_public', True)

            if not is_public:
                # private 계정 → 본인이거나 팔로워만 허용
                if requester == handle:
                    pass  # 본인은 OK
                elif requester:
                    follow_check = db.collection('follows').where('from_handle', '==', requester).where('to_handle', '==', handle).limit(1).get()
                    if not follow_check:
                        return jsonify({'posts': [], 'private': True})
                else:
                    return jsonify({'posts': [], 'private': True})

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
    password = data.get('password')
    is_public = data.get('is_public', True)
    device = data.get('device', 'phone')

    if not phone or not username or not handle or not password:
        return jsonify({'error': 'phone, username, handle, password required'}), 400

    if len(password) < 6:
        return jsonify({'error': 'password must be at least 6 characters'}), 400

    existing = db.collection('users').where('handle', '==', handle).limit(1).get()
    if existing:
        return jsonify({'error': 'handle already taken'}), 409

    user = {
        'phone': phone,
        'username': username,
        'handle': handle,
        'password_hash': hash_password(password),
        'is_public': is_public,
        'device': device,
        'post_count': 0,
        'follower_count': 0,
        'following_count': 0,
        'profile_photo': None,
        'email_code': generate_email_code(),
        'created_at': datetime.utcnow()
    }

    doc_ref = db.collection('users').add(user)
    email_address = f"{handle}.{user['email_code']}@{EMAIL_DOMAIN}"
    return jsonify({
        'success': True,
        'user_id': doc_ref[1].id,
        'email_address': email_address,
        'message': f'Welcome to off/on, {username}!'
    })


@app.route('/api/login', methods=['POST'])
def login():
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    data = request.json
    handle = data.get('handle', '').strip().replace('@', '')
    password = data.get('password', '')

    if not handle or not password:
        return jsonify({'error': 'handle and password required'}), 400

    users = db.collection('users').where('handle', '==', handle).limit(1).get()
    if not users:
        return jsonify({'error': 'user not found'}), 404

    user = users[0].to_dict()

    # 기존 유저 (비밀번호 없는) → 전화번호로 검증
    if not user.get('password_hash'):
        phone = data.get('phone', '')
        if not phone or user.get('phone') != phone.replace(' ', ''):
            return jsonify({'error': 'invalid credentials'}), 401
        # 비밀번호 설정해줌
        users[0].reference.update({'password_hash': hash_password(password)})
    else:
        if user['password_hash'] != hash_password(password):
            return jsonify({'error': 'incorrect password'}), 401

    user.pop('phone', None)
    user.pop('password_hash', None)
    user['created_at'] = user['created_at'].isoformat() if user.get('created_at') else None
    if user.get('last_posted'):
        user['last_posted'] = user['last_posted'].isoformat()

    return jsonify({'success': True, 'user': user})


@app.route('/api/report', methods=['POST'])
def report_post():
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    data = request.json
    post_id = data.get('post_id')
    reporter = data.get('reporter_handle', 'anonymous')
    reason = data.get('reason', 'no reason given')

    if not post_id:
        return jsonify({'error': 'post_id required'}), 400

    post = db.collection('posts').document(post_id).get()
    if not post.exists:
        return jsonify({'error': 'post not found'}), 404

    post_data = post.to_dict()

    db.collection('reports').add({
        'post_id': post_id,
        'reporter': reporter,
        'reason': reason,
        'post_handle': post_data.get('handle'),
        'post_text': post_data.get('text', '')[:200],
        'created_at': datetime.utcnow(),
        'resolved': False
    })

    return jsonify({'success': True})




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


@app.route('/api/follow', methods=['POST'])
def follow():
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    data = request.json
    from_handle = data.get('from_handle')
    to_handle = data.get('to_handle')

    if not from_handle or not to_handle or from_handle == to_handle:
        return jsonify({'error': 'invalid handles'}), 400

    # 이미 팔로우 중인지 확인
    existing = db.collection('follows').where('from_handle', '==', from_handle).where('to_handle', '==', to_handle).limit(1).get()
    if existing:
        return jsonify({'error': 'already following'}), 409

    # 팔로우 관계 저장
    db.collection('follows').add({
        'from_handle': from_handle,
        'to_handle': to_handle,
        'created_at': datetime.utcnow()
    })

    # 카운트 업데이트
    from_users = db.collection('users').where('handle', '==', from_handle).limit(1).get()
    to_users = db.collection('users').where('handle', '==', to_handle).limit(1).get()
    if from_users:
        from_users[0].reference.update({'following_count': firestore.Increment(1)})
    if to_users:
        to_users[0].reference.update({'follower_count': firestore.Increment(1)})

    # 알림 생성
    db.collection('notifications').add({
        'to_handle': to_handle,
        'from_handle': from_handle,
        'type': 'follow',
        'read': False,
        'created_at': datetime.utcnow()
    })

    return jsonify({'success': True})


@app.route('/api/unfollow', methods=['POST'])
def unfollow():
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    data = request.json
    from_handle = data.get('from_handle')
    to_handle = data.get('to_handle')

    follows = db.collection('follows').where('from_handle', '==', from_handle).where('to_handle', '==', to_handle).limit(1).get()
    if not follows:
        return jsonify({'error': 'not following'}), 404

    follows[0].reference.delete()

    from_users = db.collection('users').where('handle', '==', from_handle).limit(1).get()
    to_users = db.collection('users').where('handle', '==', to_handle).limit(1).get()
    if from_users:
        from_users[0].reference.update({'following_count': firestore.Increment(-1)})
    if to_users:
        to_users[0].reference.update({'follower_count': firestore.Increment(-1)})

    return jsonify({'success': True})


@app.route('/api/is_following', methods=['GET'])
def is_following():
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    from_handle = request.args.get('from_handle')
    to_handle = request.args.get('to_handle')

    existing = db.collection('follows').where('from_handle', '==', from_handle).where('to_handle', '==', to_handle).limit(1).get()
    return jsonify({'is_following': len(existing) > 0})


@app.route('/api/post/<post_id>', methods=['DELETE'])
def delete_post(post_id):
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    handle = request.args.get('handle')
    if not handle:
        return jsonify({'error': 'handle required'}), 400

    post_ref = db.collection('posts').document(post_id)
    post = post_ref.get()

    if not post.exists:
        return jsonify({'error': 'post not found'}), 404

    if post.to_dict().get('handle') != handle:
        return jsonify({'error': 'unauthorized'}), 403

    post_ref.delete()

    # post_count 감소
    users = db.collection('users').where('handle', '==', handle).limit(1).get()
    if users:
        users[0].reference.update({'post_count': firestore.Increment(-1)})

    return jsonify({'success': True})



def get_followers(handle):
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    follows = db.collection('follows').where('to_handle', '==', handle).get()
    handles = [f.to_dict()['from_handle'] for f in follows]
    return jsonify({'followers': handles})


@app.route('/api/following/<handle>', methods=['GET'])
def get_following(handle):
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    follows = db.collection('follows').where('from_handle', '==', handle).get()
    handles = [f.to_dict()['to_handle'] for f in follows]
    return jsonify({'following': handles})


@app.route('/email', methods=['POST'])
def receive_email():
    if not db:
        return 'server error', 500

    import re as _re
    
    # 디버그 로그
    print("=== EMAIL RECEIVED ===")
    print("Form keys:", list(request.form.keys()))
    print("to:", request.form.get('to', ''))
    print("subject:", request.form.get('subject', ''))
    print("text:", request.form.get('text', '')[:200])
    print("attachments:", request.form.get('attachments', '0'))
    print("attachment-info:", request.form.get('attachment-info', ''))
    print("======================")

    to_email = request.form.get('to', '') + ' ' + request.form.get('envelope', '')
    subject = request.form.get('subject', '').strip()
    body = request.form.get('text', '').strip()
    if not body:
        body = request.form.get('html', '').strip()
        # HTML 태그 제거
        body = _re.sub(r'<[^>]+>', '', body).strip()

    match = _re.search(r'([\w]+)\.([\w]+)@mail\.offon\.app', to_email)
    if not match:
        print("No match for email address:", to_email)
        return 'ok', 200

    handle = match.group(1)
    code = match.group(2)

    user_query = db.collection('users').where('handle', '==', handle).where('email_code', '==', code).limit(1).get()
    if not user_query:
        print("No user found for handle:", handle, "code:", code)
        return 'ok', 200

    user_doc = user_query[0]
    user_data = user_doc.to_dict()

    # 첨부 이미지 최대 1개
    media_url = None
    media_type = None
    attachment_count = int(request.form.get('attachments', 0))
    if attachment_count > 0:
        try:
            import json as _json
            info = _json.loads(request.form.get('attachment-info', '{}'))
            print("attachment-info parsed:", info)
            first_key = list(info.keys())[0]
            media_type = info[first_key].get('type', '')
            filename = info[first_key].get('filename', 'image.jpg')
            if media_type.startswith('image/') and R2_ACCESS_KEY:
                attachment_file = request.files.get(first_key)
                if attachment_file:
                    print("Uploading image:", filename, media_type)
                    media_url = upload_image_to_r2(attachment_file, filename, media_type)
                    print("Uploaded URL:", media_url)
            else:
                media_type = None
        except Exception as e:
            print("Attachment error:", e)

    # body만 사용, subject 제외
    text = body.strip() if body else ''

    post = {
        'user_id': user_doc.id,
        'username': user_data.get('username', 'anonymous'),
        'handle': user_data.get('handle', 'unknown'),
        'is_public': user_data.get('is_public', True),
        'text': text,
        'media_url': media_url,
        'media_type': media_type,
        'device': user_data.get('device', 'email'),
        'created_at': datetime.utcnow(),
        'likes': 0,
        'source': 'email'
    }

    db.collection('posts').add(post)
    user_doc.reference.update({
        'post_count': firestore.Increment(1),
        'last_posted': datetime.utcnow()
    })

    print("Post created successfully for:", handle)
    return 'ok', 200


@app.route('/api/post/<post_id>/like', methods=['POST'])
def like_post(post_id):
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    handle = request.json.get('handle') if request.json else None
    if not handle:
        return jsonify({'error': 'handle required'}), 400

    existing = db.collection('likes').where('post_id', '==', post_id).where('handle', '==', handle).limit(1).get()
    if existing:
        existing[0].reference.delete()
        db.collection('posts').document(post_id).update({'likes': firestore.Increment(-1)})
        return jsonify({'liked': False})

    db.collection('likes').add({'post_id': post_id, 'handle': handle, 'created_at': datetime.utcnow()})
    db.collection('posts').document(post_id).update({'likes': firestore.Increment(1)})

    # 알림 생성 (본인 포스트 아닐 때)
    post = db.collection('posts').document(post_id).get()
    if post.exists:
        post_data = post.to_dict()
        post_owner = post_data.get('handle')
        if post_owner and post_owner != handle:
            db.collection('notifications').add({
                'to_handle': post_owner,
                'from_handle': handle,
                'type': 'like',
                'post_id': post_id,
                'post_text': post_data.get('text', '')[:50],
                'read': False,
                'created_at': datetime.utcnow()
            })

    return jsonify({'liked': True})


@app.route('/api/liked_posts/<handle>', methods=['GET'])
def get_liked_posts(handle):
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    likes = db.collection('likes').where('handle', '==', handle).order_by('created_at', direction=firestore.Query.DESCENDING).limit(20).get()
    post_ids = [l.to_dict()['post_id'] for l in likes]

    posts = []
    for post_id in post_ids:
        doc = db.collection('posts').document(post_id).get()
        if doc.exists:
            post = doc.to_dict()
            post['id'] = doc.id
            post['created_at'] = post['created_at'].isoformat() if post.get('created_at') else None
            posts.append(post)

    return jsonify({'posts': posts})


@app.route('/api/notifications/<handle>', methods=['GET'])
def get_notifications(handle):
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    notifs_raw = db.collection('notifications').where('to_handle', '==', handle).limit(30).get()
    result = []
    for n in notifs_raw:
        d = n.to_dict()
        d['id'] = n.id
        d['created_at'] = d['created_at'].isoformat() if d.get('created_at') else None
        result.append(d)
    # 클라이언트에서 정렬
    result.sort(key=lambda x: x.get('created_at', ''), reverse=True)

    return jsonify({'notifications': result})


@app.route('/api/notifications/read', methods=['POST'])
def mark_notifications_read(handle=None):
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    handle = request.json.get('handle') if request.json else None
    if not handle:
        return jsonify({'error': 'handle required'}), 400

    notifs = db.collection('notifications').where('to_handle', '==', handle).where('read', '==', False).get()
    for n in notifs:
        n.reference.update({'read': True})

    return jsonify({'success': True})



def generate_user_email_code():
    if not db:
        return jsonify({'error': 'db not ready'}), 500

    data = request.json
    handle = data.get('handle')
    if not handle:
        return jsonify({'error': 'handle required'}), 400

    users = db.collection('users').where('handle', '==', handle).limit(1).get()
    if not users:
        return jsonify({'error': 'user not found'}), 404

    user_doc = users[0]
    user_data = user_doc.to_dict()

    if user_data.get('email_code'):
        email_address = f"{handle}.{user_data['email_code']}@{EMAIL_DOMAIN}"
        return jsonify({'email_address': email_address})

    code = generate_email_code()
    user_doc.reference.update({'email_code': code})
    email_address = f"{handle}.{code}@{EMAIL_DOMAIN}"
    return jsonify({'email_address': email_address})


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
