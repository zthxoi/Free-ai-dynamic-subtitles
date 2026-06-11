import streamlit as st
import torch
from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor, pipeline
import os
import subprocess
import pysrt

MODEL_ID = "openai/whisper-small"
device = "cuda:0" if torch.cuda.is_available() else "cpu"
torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32

@st.cache_resource
def load_whisper():
    model = AutoModelForSpeechSeq2Seq.from_pretrained(
        MODEL_ID, torch_dtype=torch_dtype, low_cpu_mem_usage=True, use_safetensors=True
    ).to(device)
    processor = AutoProcessor.from_pretrained(MODEL_ID)
    pipe = pipeline(
        "automatic-speech-recognition", model=model, tokenizer=processor.tokenizer,
        feature_extractor=processor.feature_extractor, chunk_length_s=30,
        torch_dtype=torch_dtype, device=device
    )
    return pipe

def extract_audio(video, audio):
    cmd = f'ffmpeg -y -i "{video}" -vn -acodec pcm_s16le -ar 16000 -ac 1 "{audio}"'
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def make_dynamic_srt(whisper_chunks, srt_out, max_words):
    file = pysrt.SubRipFile()
    all_words = []
    for chunk in whisper_chunks:
        if "text" not in chunk or not chunk["text"].strip():
            continue
        if isinstance(chunk["timestamp"], tuple):
            start, end = chunk["timestamp"]
            all_words.append({"text": chunk["text"].strip(), "start": start, "end": end})
            
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
    style = "FontName=Arial,FontSize=26,Bold=1,PrimaryColour=&H00FFFF,OutlineColour=&H000000,BorderStyle=1,Outline=3,Alignment=2"
    srt_filter = srt_file.replace(":", "\\:")
    cmd = f'ffmpeg -y -i "{video_in}" -vf "subtitles={srt_filter}:force_style=\'{style}\'" -c:a copy "{video_out}"'
    subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

st.set_page_config(page_title="SDVGH Subtitles Generator", layout="centered")
st.title("Автоматический генератор субтитров")
st.write(f"Вычисления запущены на: **{device.upper()}**")

state = {"pipe": load_whisper()}

st.sidebar.header("Настройки субтитров")
lang = st.sidebar.selectbox("Язык видео", ["russian", "english"])
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
        
        with st.spinner("Вытаскиваем звук, распознаем и вжигаем сабы..."):
            try:
                extract_audio(input_path, audio_path)
                
                if state["pipe"] is None:
                    state["pipe"] = load_whisper()

                result = state["pipe"](audio_path, return_timestamps="word", generate_kwargs={"language": lang})
                
                make_dynamic_srt(result["chunks"], srt_path, max_words)
                
                import gc
                state["pipe"] = None
                gc.collect() 

                
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
                st.error(f"Произошла что-то плохое: {e}")
                
            finally:
                if state["pipe"] is None:
                    state["pipe"] = load_whisper()
                    
                for path in [audio_path, srt_path, input_path, output_path]:
                    if os.path.exists(path):
                        try: os.remove(path)
                        except: pass
