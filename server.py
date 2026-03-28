from flask import Flask, request, jsonify
from flask_cors import CORS
from twilio.twiml.messaging_response import MessagingResponse
import firebase_admin
from firebase_admin import credentials, firestore, storage
import os
import json
import random
import string
from datetime import datetime

app = Flask(__name__)
CORS(app)

firebase_key = os.environ.get('FIREBASE_KEY')
STORAGE_BUCKET = os.environ.get('STORAGE_BUCKET', '')

if firebase_key:
    key_dict = json.loads(firebase_key)
    cred = credentials.Certificate(key_dict)
    if STORAGE_BUCKET:
        firebase_admin.initialize_app(cred, {'storageBucket': STORAGE_BUCKET})
    else:
        firebase_admin.initialize_app(cred)
    db = firestore.client()
else:
    db = None
    print("WARNING: FIREBASE_KEY not set")

OFFON_NUMBER = os.environ.get('OFFON_NUMBER', '+18448860777')
EMAIL_DOMAIN = 'mail.offon.app'

def generate_email_code():
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=4))

def upload_image_to_storage(file_obj, filename, content_type):
    try:
        bucket = storage.bucket()
        blob = bucket.blob(f'posts/{datetime.utcnow().strftime("%Y%m%d%H%M%S")}_{filename}')
        blob.upload_from_file(file_obj, content_type=content_type)
        blob.make_public()
        return blob.public_url
    except Exception as e:
        print("Storage upload error:", e)
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
        'message': f'Welcome to off/on, {username}! Text {OFFON_NUMBER} or email {email_address} to post.'
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
            if media_type.startswith('image/') and STORAGE_BUCKET:
                attachment_file = request.files.get(first_key)
                if attachment_file:
                    print("Uploading image:", filename, media_type)
                    media_url = upload_image_to_storage(attachment_file, filename, media_type)
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


@app.route('/api/generate_email_code', methods=['POST'])
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
