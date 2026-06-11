import streamlit as st
import os
import subprocess
import requests
import pysrt

# Настройки API
GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", "")

if not GROQ_API_KEY:
    st.error("Ошибка: Добавьте GROQ_API_KEY в Secrets вашего Streamlit Cloud!")
    st.stop()

def extract_audio(video, audio):
    # Сжимаем аудио в MP3 (моно, 16кГц, 64kbps) — это экономит трафик и идеально для API Groq
    cmd = f'ffmpeg -y -i "{video}" -vn -ar 16000 -ac 1 -b:a 64k "{audio}"'
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def query_groq_whisper(audio_path):
    """Отправка аудиофайла напрямую в Groq Cloud API"""
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    
    with open(audio_path, "rb") as f:
        files = {
            "file": (os.path.basename(audio_path), f, "audio/mp3")
        }
        data = {
            "model": "whisper-large-v3",
            "response_format": "verbose_json",  # Требуем подробный формат для пословных таймкодов
            "temperature": "0.0"
        }
        response = requests.post(url, headers=headers, files=files, data=data, timeout=60)
        
    if response.status_code != 200:
        raise Exception(f"Groq API Error {response.status_code}: {response.text}")
        
    return response.json()

def make_dynamic_srt(groq_words, srt_out, max_words):
    file = pysrt.SubRipFile()
    all_words = []
    
    # Парсим структуру, которую вернул Groq API
    for w in groq_words:
        if "word" not in w or not w["word"].strip():
            continue
        all_words.append({
            "text": w["word"].strip(), 
            "start": w["start"], 
            "end": w["end"]
        })
            
    sub_index = 1
    for i in range(0, len(all_words), max_words):
        group = all_words[i:i + max_words]
        text_content = " ".join([w["text"] for w in group])
        
        start_time = pysrt.SubRipTime(seconds=group[0]["start"])
        end_time = pysrt.SubRipTime(seconds=group[-1]["end"])
        
        if start_time == end_time:
            end_time = pysrt.SubRipTime(seconds=group[-1]["end"] + 0.1)
            
        item = pysrt.SubRipItem(index=sub_index, start=start_time, end=end_time, text=text_content)
        file.append(item)
        sub_index += 1
        
    file.save(srt_out, encoding='utf-8')

def burn_subtitles(video_in, srt_file, video_out):
    style = "FontName=Arial,FontSize=16,Bold=1,PrimaryColour=&H00FFFF,OutlineColour=&H000000,BorderStyle=1,Outline=1.5,Alignment=2"
    srt_filter = srt_file.replace(":", "\\:")
    cmd = f'ffmpeg -y -i "{video_in}" -vf "subtitles={srt_filter}:force_style=\'{style}\'" -c:a copy "{video_out}"'
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# --- ИНТЕРФЕЙС ---
st.set_page_config(page_title="SDVGH Subtitles Generator", layout="centered")
st.title("Автоматический генератор субтитров")
st.write("Вычисления запущены на облачных процессорах: **Groq LPU (Whisper Large V3)**")

st.sidebar.header("Настройки субтитров")
max_words = st.sidebar.slider("Макс. слов на экране", min_value=1, max_value=5, value=2)

uploaded_file = st.file_uploader("Перетащи сюда свое MP4 видео", type=["mp4", "mov", "avi"])

if uploaded_file is not None:
    input_path = "user_input.mp4"
    with open(input_path, "wb") as f:
        f.write(uploaded_file.read())
        
    st.video(input_path) 
    
    if st.button("Сгенерировать субтитры"):
        audio_path = "user_temp_audio.mp3"  # Перевели на mp3
        srt_path = "user_subtitles.srt"
        output_path = "user_output_subs.mp4"
        
        with st.spinner("Вытаскиваем звук, отправляем в облако Groq и вжигаем сабы..."):
            try:
                # 1. Сжимаем и вытаскиваем аудиодорожку
                extract_audio(input_path, audio_path)
                
                # 2. Быстро распознаем через API
                api_result = query_groq_whisper(audio_path)
                words = api_result.get("words", [])
                
                # Запасной фолбэк, если Groq сгруппировал только по сегментам
                if not words and "segments" in api_result:
                    words = [{"word": seg.get("text"), "start": seg.get("start"), "end": seg.get("end")} for seg in api_result["segments"]]
                
                # 3. Собираем структуру через pysrt
                make_dynamic_srt(words, srt_path, max_words)
                
                # 4. Вжигаем субтитры
                burn_subtitles(input_path, srt_path, output_path)
                
                st.success("Видео успешно обработано!")
                st.video(output_path) 
                
                with open(output_path, "rb") as file:
                    st.download_button(
                        label="🎬 Скачать готовое видео",
                        data=file,
                        file_name="sdvh_subtitles.mp4",
                        mime="video/mp4"
                    )
                    
            except Exception as e:
                st.error(f"Произошла ошибка обработки: {e}")
                
            finally:
                # Чистим сервер за собой
                for path in [audio_path, srt_path, input_path, output_path]:
                    if os.path.exists(path):
                        try: os.remove(path)
                        except: pass
