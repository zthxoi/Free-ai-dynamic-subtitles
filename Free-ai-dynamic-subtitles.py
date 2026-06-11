import streamlit as st
import os
import subprocess
import requests
import time
import pysrt

# Настройки API
# Используем самую мощную и быструю на сегодня модель whisper-large-v3-turbo
API_URL = "https://api-inference.huggingface.co/models/openai/whisper-large-v3-turbo"

# Безопасно читаем токен из секретов Streamlit
if "HF_TOKEN" in st.secrets:
    headers = {"Authorization": f"Bearer {st.secrets['HF_TOKEN']}"}
else:
    st.error("Ошибка: Настройте HF_TOKEN в Secrets вашего Streamlit Cloud!")
    st.stop()

def extract_audio(video_path, audio_path):
    cmd = ['ffmpeg', '-y', '-i', video_path, '-q:a', '0', '-map', 'a', '-ar', '16000', audio_path]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def query_whisper_api(filename):
    """Отправляет аудиофайл на сервера Hugging Face с защитой от сбоев сети"""
    with open(filename, "rb") as f:
        data = f.read()
    
    params = {"return_timestamps": "word"}
    
    # Делаем 5 попыток пробить сеть, если сервер хостинга тупит
    for attempt in range(1, 6):
        try:
            # Добавляем timeout=30, чтобы запрос не зависал намертво
            response = requests.post(API_URL, headers=headers, data=data, params=params, timeout=30)
            result = response.json()
            
            # Проверяем, не просыпается ли модель
            if isinstance(result, dict) and "error" in result and "currently loading" in result["error"]:
                st.warning(f"⏳ Модель Hugging Face просыпается... Ждем (попытка {attempt}/5)")
                time.sleep(8)
                continue
                
            return result
            
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as net_err:
            # Если упал DNS или моргнул интернет на хостинге — не падаем, а ждем и повторяем
            if attempt == 5:
                raise Exception(f"Сетевой сбой хостинга после 5 попыток: {net_err}")
            time.sleep(3)  # Пауза перед следующей попыткой
            continue
            
    raise Exception("Не удалось получить ответ от API.")

def make_dynamic_srt(chunks, srt_path, max_words=2):
    if not chunks:
        return
        
    # Создаем пустой объект файла субтитров
    srt = pysrt.SubRipFile()
    index = 1
    
    for i in range(0, len(chunks), max_words):
        group = chunks[i:i + max_words]
        
        text = " ".join([word_info.get("text", "").strip() for word_info in group])
        if not text:
            continue
            
        try:
            start_time = group[0]["timestamp"][0]
            end_time = group[-1]["timestamp"][1]
        except (KeyError, TypeError, IndexError):
            continue
            
        if start_time is None or end_time is None:
            continue

        # Переводим секунды в строковый формат, который pysrt понимает идеально: "ЧЧ:ММ:СС,МММ"
        def format_time(seconds):
            hrs = int(seconds // 3600)
            mins = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            msecs = int((seconds - int(seconds)) * 1000)
            return f"{hrs:02d}:{mins:02d}:{secs:02d},{msecs:03d}"

        # Создаем объект субтитра из обычной текстовой строки формата SRT
        # Это избавляет нас от необходимости импортировать SubRipEvent и SubRipTime
        srt_item_text = f"{index}\n{format_time(start_time)} --> {format_time(end_time)}\n{text}\n"
        
        # Парсим строку в объект и добавляем в файл
        srt.append(pysrt.SubRipItem.from_string(srt_item_text))
        index += 1
        
    srt.save(srt_path, encoding='utf-8')

def burn_subtitles(video_path, srt_path, output_path):
    cmd = [
        'ffmpeg', '-y', '-i', video_path,
        '-vf', f"subtitles={srt_path}:force_style='FontName=Arial,FontSize=16,PrimaryColour=&H00FFFF,OutlineColour=&H000000,BorderStyle=1,Outline=1.5,Shadow=0,Alignment=2'",
        '-c:a', 'copy',
        output_path
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode != 0:
        raise Exception(f"FFmpeg error: {result.stderr}")

# --- ИНТЕРФЕЙС STREAMLIT ---
st.title("🎬 Автоматические динамические субтитры (Cloud API)")
st.caption("Курсовой проект ПМИ. Обработка через Hugging Face Serverless Inference")

st.sidebar.header("Настройки субтитров")
max_words = st.sidebar.slider("Макс. слов на экране", min_value=1, max_value=5, value=2)

uploaded_file = st.file_uploader("Перетащи сюда свое MP4 видео", type=["mp4", "mov", "avi"])

if uploaded_file is not None:
    input_path = "user_input.mp4"
    with open(input_path, "wb") as f:
        f.write(uploaded_file.read())
        
    st.video(input_path) 
    
    if st.button("Сгенерировать субтитры"):
        audio_path = "user_temp_audio.wav"
        srt_path = "user_subtitles.srt"
        output_path = "user_output_subs.mp4"
        
        with st.spinner("Отправляем аудио на сервера Hugging Face, распознаем и вжигаем идеальные сабы..."):
            try:
                # 1. Извлекаем звук локально
                extract_audio(input_path, audio_path)
                
                # 2. Отправляем в облако HF
                api_result = query_whisper_api(audio_path)
                
                if "chunks" not in api_result:
                    # Если API вернул просто текст, а не чанки с таймкодами
                    if "text" in api_result:
                        st.warning("Модель вернула сплошной текст без тайм-кодов слов. Возможно, аудио слишком короткое.")
                    else:
                        raise Exception(f"Некорректный ответ API: {api_result}")
                
                # 3. Собираем SRT локально
                chunks = api_result.get("chunks", [])
                make_dynamic_srt(chunks, srt_path, max_words)
                
                # 4. Вжигаем субтитры через локальный FFmpeg
                burn_subtitles(input_path, srt_path, output_path)
                
                st.success("Видео успешно обработано с максимальным качеством!")
                st.video(output_path) 
                
                with open(output_path, "rb") as file:
                    st.download_button(
                        label="🎬 Скачать готовое видео",
                        data=file,
                        file_name="dynamic_subtitles_large.mp4",
                        mime="video/mp4"
                    )
                    
            except Exception as e:
                st.error(f"Произошла ошибка: {e}")
                
            finally:
                # Чистим мусор
                for path in [audio_path, srt_path, input_path, output_path]:
                    if os.path.exists(path):
                        try: os.remove(path)
                        except: pass
