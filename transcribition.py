import whisper
import os

# Загружаем модель (base — быстрая, medium — точная)
model = whisper.load_model("base")


@app.route('/upload_audio', methods=['POST'])
@login_required
def upload_audio():
    file = request.files.get('audio')
    chat_id = request.form.get('chat_id')

    if file:
        # 1. Сохраняем файл временно
        path = os.path.join("uploads", file.filename)
        file.save(path)

        # 2. Транскрибируем (для разделения ролей в Whisper есть свои хитрости)
        # В простом варианте Whisper выдает текст.
        # Для разделения спикеров (диаризации) обычно используют библиотеку pyannote.audio
        result = model.transcribe(path)

        db_sess = db_session.create_session()

        # Эмуляция разделения (упрощенно для примера)
        # В реальности здесь будет цикл по сегментам аудио
        for segment in result['segments']:
            # Здесь логика определения: если текст содержит "Здравствуйте, я оператор",
            # помечаем как оператора, иначе как клиента.
            # На хакатоне можно сделать "заглушку" или использовать pyannote

            new_msg = Messages(
                chat_id=chat_id,
                sender_id=current_user.id,  # Или ID бота/оператора
                content=segment['text']
            )
            db_sess.add(new_msg)
            db_sess.commit()

            # Рассылаем в чат через SocketIO
            socketio.emit('render_msg', {
                'content': segment['text'],
                'sender_id': current_user.id,
                'time': "Звонок"
            }, to=str(chat_id))

        return {"status": "ok"}