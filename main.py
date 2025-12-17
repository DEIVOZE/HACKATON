from datetime import datetime

from flask import Flask, render_template, redirect, request, session, url_for, Response
from flask_socketio import SocketIO, join_room, emit

from data import db_session
from data.chats import Chats
from data.messages import Messages
from data.users import User
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
import whisper
import os
import google.generativeai as genai

with open('API_KEY.txt', 'r') as file_API:
    genai.configure(api_key=f"{file_API.readline().rstrip()}")
model_GEMINI = genai.GenerativeModel('gemini-2.5-flash')

model_whisper = whisper.load_model("base")

app = Flask(__name__)
app.config['SECRET_KEY'] = 'hackaton_GMI'
login_manager = LoginManager()
login_manager.init_app(app)
socketio = SocketIO(app)


@socketio.on('join')
def on_join(data):
    room = str(data['chat_id'])
    join_room(room)


@socketio.on('send_msg_rpc')
def handle_message(data):
    chat_id = data['chat_id']
    text = data['text']

    db_sess = db_session.create_session()
    user = db_sess.query(User).get(current_user.id)

    new_msg = Messages(
        chat_id=chat_id,
        sender_id=user.id,
        content=text
    )
    db_sess.add(new_msg)
    db_sess.commit()

    emit('render_msg', {
        'content': text,
        'sender_id': user.id,
        'time': new_msg.created_at.strftime('%H:%M')
    }, to=str(chat_id))
    db_sess.close()


@login_manager.user_loader
def load_user(user_id):
    db_sess = db_session.create_session()
    return db_sess.query(User).get(user_id)


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        is_operator_selected = True if request.form.get('is_admin') == 'on' else False

        db_sess = db_session.create_session()
        user = db_sess.query(User).filter(User.name == username).first()
        if not user:
            user = User(
                name=username,
                is_operator=is_operator_selected
            )
            db_sess.add(user)
            db_sess.commit()

        login_user(user)

        return redirect(url_for('index'))
    return render_template("login.html")


@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))


@app.route("/")
def index():
    if current_user.is_authenticated:
        db_sess = db_session.create_session()
        if current_user.is_operator:
            chats = db_sess.query(Chats).all()
        else:
            chats = db_sess.query(Chats).filter(Chats.id_user == current_user.id).all()
        print(current_user.is_operator)
        return render_template("index.html", current_user=current_user, chats=chats)
    return redirect(url_for("login"))


@app.route("/new_chat")
def add_new_chat():
    db_sess = db_session.create_session()
    new_chat = Chats(
        name=current_user.name,
        id_user=current_user.id,
    )
    db_sess.add(new_chat)
    db_sess.commit()
    return redirect(url_for("index"))


@app.route('/chat/<int:chat_id>')
@login_required
def chatt(chat_id):
    db_sess = db_session.create_session()
    messages = db_sess.query(Messages).filter(Messages.chat_id == chat_id).order_by(Messages.created_at).all()
    chat = db_sess.query(Chats).get(chat_id)
    return render_template("chat.html", messages=messages, chat_id=chat_id, chat=chat)


@app.route('/send_message/<int:chat_id>', methods=['POST'])
def send_message(chat_id):
    text = request.form.get('text')
    db_sess = db_session.create_session()

    new_msg = Messages(
        chat_id=chat_id,
        sender_id=current_user.id,
        content=text
    )
    db_sess.add(new_msg)
    db_sess.commit()
    return redirect(url_for('chatt', chat_id=chat_id))



@app.route('/upload_audio', methods=['POST'])
@login_required
def upload_audio():
    file = request.files.get('audio')
    chat_id = request.form.get('chat_id')

    if not file:
        return {"status": "error"}, 400

    if not os.path.exists('uploads'): os.mkdir('uploads')
    path = os.path.join("uploads", f"temp_{chat_id}.wav")
    file.save(path)

    result = model_whisper.transcribe(path, language="ru", fp16=False)

    db_sess = db_session.create_session()

    current_role = "operator"

    for segment in result['segments']:
        text = segment['text'].strip()
        if not text: continue

        if current_role == "client":
            s_id = current_user.id
        else:
            s_id = 0

        new_msg = Messages(
            chat_id=chat_id,
            sender_id=s_id,
            content=text
        )
        db_sess.add(new_msg)
        db_sess.commit()

        socketio.emit('render_msg', {
            'content': text,
            'sender_id': s_id,
            'time': datetime.now().strftime('%H:%M')
        }, to=str(chat_id))

        current_role = "operator" if current_role == "client" else "client"

    db_sess.close()
    return {"status": "ok"}


@app.route('/download_report/<int:chat_id>')
@login_required
def download_report(chat_id):
    db_sess = db_session.create_session()
    messages = db_sess.query(Messages).filter(Messages.chat_id == chat_id).order_by(Messages.created_at).all()

    report_lines = [f"ОТЧЕТ ПО ЧАТУ №{chat_id}", "=" * 30]

    for msg in messages:
        if msg.sender_id == 0:
            role = "ОПЕРАТОР"
        else:
            role = "КЛИЕНТ"

        time_str = msg.created_at.strftime('%Y-%m-%d %H:%M')
        report_lines.append(f"[{time_str}] {role}: {msg.content}")

    report_text = "\n".join(report_lines)

    return Response(
        report_text,
        mimetype="text/plain",
        headers={"Content-disposition": f"attachment; filename=report_chat_{chat_id}.txt"}
    )


@app.route('/toggle_ai/<int:chat_id>', methods=['POST'])
@login_required
def toggle_ai(chat_id):
    data = request.json
    db_sess = db_session.create_session()
    chat = db_sess.query(Chats).get(chat_id)
    chat.is_ai_active = data.get('active', False)
    db_sess.commit()
    return {"status": "ok"}


def get_ai_answer(chat_id, user_text):
    db_sess = db_session.create_session()
    history = db_sess.query(Messages).filter(Messages.chat_id == chat_id) \
        .order_by(Messages.created_at.desc()).limit(5).all()

    context = ""
    for m in reversed(history):
        role = "Оператор" if m.sender_id == 0 else "Клиент"
        context += f"{role}: {m.content}\n"

    prompt = f"""Ты — опытный оператор техподдержки. Но при этом ты знаешь что ты нейросеть
    Вот контекст последних сообщений:
    {context}

    Клиент говорит: {user_text}

    Твоя задача: вежливо и кратко ответить клиенту, исходя из контекста выше. 
    Но при этом ты притворяешься оператором, а являешься ИИ помошником"""
    try:
        response = model_GEMINI.generate_content(prompt)
        db_sess.close()
        return f"<b>Ответ от ИИ</b>: {response.text}"
    except Exception as e:
        print(e)
        db_sess.close()
        return "Извините, нейросеть в данный момент ответить не может, сейчас к вам подключится оператор"


@socketio.on('send_msg_rpc')
def handle_message(data):
    chat_id = data['chat_id']
    text = data['text']
    db_sess = db_session.create_session()

    new_msg = Messages(chat_id=chat_id, sender_id=current_user.id, content=text)
    db_sess.add(new_msg)
    db_sess.commit()

    current_time = datetime.now().strftime('%H:%M')

    emit('render_msg', {'content': text, 'sender_id': current_user.id, 'time': current_time}, to=str(chat_id))

    chat = db_sess.query(Chats).get(chat_id)
    if not current_user.is_operator and chat.is_ai_active:
        ai_text = get_ai_answer(chat_id, text)

        ai_msg = Messages(chat_id=chat_id, sender_id=0, content=ai_text)
        db_sess.add(ai_msg)
        db_sess.commit()

        emit('render_msg', {'content': ai_text, 'sender_id': 0, 'time': 'ИИ'}, to=str(chat_id))


def main():
    db_session.global_init("db/hackaton.db")


if __name__ == '__main__':
    main()
    socketio.run(app, host='0.0.0.0', port=8000, allow_unsafe_werkzeug=True)
