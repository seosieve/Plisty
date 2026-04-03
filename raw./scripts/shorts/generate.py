#!/usr/bin/env python3
"""
raw. Shorts Generator
배경 루프 영상 + 음악 구간 + 비주얼라이저 → 쇼츠 영상
사용법: python3 generate.py <EP 폴더> <곡이름> <시작초> [끝초]
예: python3 generate.py ../EP01_260402 "치고달려라 2010" 30 90
"""

import os
import re
import sys
import subprocess
import tempfile
import shutil

import numpy as np
from PIL import Image

# ============================================================
# 설정
# ============================================================
WIDTH = 1080
HEIGHT = 1920
FPS = 30
MAX_DURATION = 60.0
AUDIO_FADE_IN = 1.0
AUDIO_FADE_OUT = 4.0
AUDIO_FADE_OUT_MIN = 0.2  # 최종 볼륨 20%
AUDIO_EXTENSIONS = {'.wav', '.mp3', '.flac', '.m4a', '.aac'}

# Veo 워터마크 패치 (background.png에서 해당 영역을 잘라 덮어씌움)
VEO_X, VEO_Y, VEO_W, VEO_H = 960, 1845, 120, 75

# 바 비주얼라이저 설정
VIS_NUM_BARS = 32
VIS_BAR_WIDTH = 6
VIS_BAR_GAP = 6
VIS_BAR_MAX_HEIGHT = 160
VIS_BAR_MIN_HEIGHT = 2
VIS_BAR_ALPHA = 0.3
VIS_BAR_COLOR = (254, 255, 245)  # #FEFFF5
VIS_MARGIN_RIGHT = 180  # 오른쪽에서 180px
VIS_MARGIN_BOTTOM = 280
VIS_FADE_IN = 2.0   # 시작 후 2초에 걸쳐 나타남
VIS_FADE_OUT = 2.0  # 끝나기 2초 전부터 사라짐
VIS_SMOOTHING = 0.3

# 그레인 설정
GRAIN_STRENGTH = 0.15
GRAIN_SIZE = 2
GRAIN_COLOR = 0.5
GRAIN_INTERVAL = 15  # 그레인 변경 주기 (프레임 수, 15=초당 2회)


# ============================================================
# 유틸리티
# ============================================================
def get_song_file(songs_dir, song_name):
    for f in os.listdir(songs_dir):
        ext = os.path.splitext(f)[1].lower()
        if ext not in AUDIO_EXTENSIONS:
            continue
        name = re.sub(r'^\d+_', '', os.path.splitext(f)[0])
        if name == song_name:
            return os.path.join(songs_dir, f)
    return None


def get_duration(filepath):
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', filepath],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def precompute_bar_heights(audio_path, total_frames):
    result = subprocess.run([
        'ffmpeg', '-i', audio_path,
        '-f', 's16le', '-ac', '1', '-ar', '48000', '-v', 'quiet', '-'
    ], capture_output=True)
    samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    sample_rate = 48000
    samples_per_frame = sample_rate // FPS

    all_heights = np.zeros((total_frames, VIS_NUM_BARS), dtype=np.float32)
    for frame_idx in range(total_frames):
        start = frame_idx * samples_per_frame
        end = min(start + samples_per_frame * 2, len(samples))
        if start >= len(samples):
            break
        chunk = samples[start:end]
        if len(chunk) < 256:
            continue

        volume = np.sqrt(np.mean(chunk ** 2))
        window = np.hanning(len(chunk))
        fft = np.abs(np.fft.rfft(chunk * window))
        fft = fft[:len(fft) // 2]
        if len(fft) == 0:
            continue

        usable_range = len(fft) // 10
        freq_bins = np.linspace(1, usable_range, VIS_NUM_BARS + 1, dtype=int)
        freq_bins = np.unique(np.clip(freq_bins, 0, len(fft) - 1))

        heights = []
        for i in range(min(len(freq_bins) - 1, VIS_NUM_BARS)):
            s, e = freq_bins[i], freq_bins[i + 1]
            if s == e:
                e = s + 1
            heights.append(np.mean(fft[s:e]))
        while len(heights) < VIS_NUM_BARS:
            heights.append(0)

        h = np.array(heights[:VIS_NUM_BARS])
        max_val = np.max(h)
        if max_val > 0:
            h = h / max_val
        all_heights[frame_idx] = h * volume

    max_val = np.max(all_heights)
    if max_val > 0:
        all_heights /= max_val
    all_heights = np.power(all_heights, 0.7)

    smoothed = np.zeros_like(all_heights)
    prev = np.zeros(VIS_NUM_BARS)
    for i in range(total_frames):
        prev = prev * (1 - VIS_SMOOTHING) + all_heights[i] * VIS_SMOOTHING
        smoothed[i] = prev
    return smoothed


def make_grain(h, w):
    if GRAIN_SIZE > 1:
        mono = np.random.randn(h // GRAIN_SIZE + 1, w // GRAIN_SIZE + 1, 1).astype(np.float32)
        color = np.random.randn(h // GRAIN_SIZE + 1, w // GRAIN_SIZE + 1, 3).astype(np.float32)
        small = mono * (1 - GRAIN_COLOR) + color * GRAIN_COLOR
        return np.repeat(np.repeat(small, GRAIN_SIZE, axis=0), GRAIN_SIZE, axis=1)[:h, :w]
    else:
        mono = np.random.randn(h, w, 1).astype(np.float32)
        color = np.random.randn(h, w, 3).astype(np.float32)
        return mono * (1 - GRAIN_COLOR) + color * GRAIN_COLOR


# ============================================================
# 메인
# ============================================================
def main():
    if len(sys.argv) < 4:
        print("사용법: python3 generate.py <EP 폴더> <곡이름> <시작초> [끝초]")
        print('예: python3 generate.py ../EP01_260402 "치고달려라 2010" 30 90')
        sys.exit(1)

    ep_dir = os.path.abspath(sys.argv[1])
    song_name = sys.argv[2]
    start_sec = float(sys.argv[3])
    end_sec = float(sys.argv[4]) if len(sys.argv) >= 5 else None

    songs_dir = os.path.join(ep_dir, "songs")
    shorts_dir = os.path.join(ep_dir, "shorts")
    bg_video = os.path.join(shorts_dir, f"{song_name}.mp4")
    bg_image = os.path.join(shorts_dir, "background.png")
    output_file = os.path.join(shorts_dir, f"{song_name}_output.mp4")

    print("🎬 raw. Shorts Generator")
    print("========================================")

    if not os.path.exists(bg_video):
        print(f"❌ 배경 영상 없음: {bg_video}")
        return

    song_file = get_song_file(songs_dir, song_name)
    if not song_file:
        print(f"❌ 곡 파일 없음: {song_name}")
        return

    song_duration = get_duration(song_file)
    end_sec = min(end_sec or song_duration, song_duration)
    clip_duration = min(end_sec - start_sec, MAX_DURATION)
    if clip_duration <= 0:
        print(f"❌ 시작점({start_sec}s)이 끝점({end_sec:.1f}s)을 초과")
        return

    total_frames = int(clip_duration * FPS)

    print(f"🎵 곡: {song_name}")
    print(f"⏱  구간: {int(start_sec//60)}:{int(start_sec%60):02d} ~ {int((start_sec+clip_duration)//60)}:{int((start_sec+clip_duration)%60):02d} ({clip_duration:.1f}초)")
    print(f"🖼  배경: {get_duration(bg_video):.1f}초 루프")

    # 오디오 구간 추출
    temp_dir = tempfile.mkdtemp(prefix='shorts_render_')
    audio_clip = os.path.join(temp_dir, 'audio_clip.wav')
    subprocess.run([
        'ffmpeg', '-y', '-ss', str(start_sec), '-t', str(clip_duration), '-i', song_file,
        '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le',
        '-af', f'afade=t=in:st=0:d={AUDIO_FADE_IN},volume=\'1-{1-AUDIO_FADE_OUT_MIN}*clip((t-{clip_duration - AUDIO_FADE_OUT})/{AUDIO_FADE_OUT},0,1)\':eval=frame',
        audio_clip
    ], capture_output=True)
    print("🎵 오디오 추출 완료")

    # 비주얼라이저 바 높이 계산
    print("📊 비주얼라이저 계산 중...")
    all_bar_heights = precompute_bar_heights(audio_clip, total_frames)
    bar_color_f = np.array(VIS_BAR_COLOR, dtype=np.float32) / 255.0
    vis_bar_y_bottom = HEIGHT - VIS_MARGIN_BOTTOM
    vis_total_width = VIS_NUM_BARS * (VIS_BAR_WIDTH + VIS_BAR_GAP) - VIS_BAR_GAP
    vis_left = WIDTH - VIS_MARGIN_RIGHT - vis_total_width
    vis_bar_positions = [vis_left + i * (VIS_BAR_WIDTH + VIS_BAR_GAP) for i in range(VIS_NUM_BARS)]
    vis_x_start = vis_bar_positions[0]
    vis_x_end = vis_bar_positions[-1] + VIS_BAR_WIDTH
    print("  ✅ 완료")

    # 배경 프레임 추출 (delogo 없이 scale만)
    print("🖼  배경 프레임 추출 중...")
    bg_frames_dir = os.path.join(temp_dir, 'bg_frames')
    os.makedirs(bg_frames_dir)
    subprocess.run([
        'ffmpeg', '-y', '-i', bg_video,
        '-vf', f'scale={WIDTH}:{HEIGHT}',
        '-pix_fmt', 'rgb24',
        os.path.join(bg_frames_dir, 'frame_%05d.raw')
    ], capture_output=True)

    frame_size = WIDTH * HEIGHT * 3
    bg_frames = []
    for fname in sorted(os.listdir(bg_frames_dir)):
        with open(os.path.join(bg_frames_dir, fname), 'rb') as f:
            data = f.read()
            if len(data) == frame_size:
                bg_frames.append(np.frombuffer(data, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3))
    bg_frame_count = len(bg_frames)
    print(f"  ✅ {bg_frame_count}프레임 로드")

    if bg_frame_count == 0:
        print("❌ 배경 프레임 추출 실패")
        shutil.rmtree(temp_dir)
        return

    # 워터마크 패치 준비
    patch = None
    if os.path.exists(bg_image):
        bg_img = np.array(Image.open(bg_image).convert('RGB'))
        patch = bg_img[VEO_Y:VEO_Y+VEO_H, VEO_X:VEO_X+VEO_W].copy()
        print("🩹 워터마크 패치 준비 완료")

    cached_grain = {}

    # FFmpeg 파이프
    print("\n🎬 영상 생성 중...")
    ffmpeg_cmd = [
        'ffmpeg', '-y',
        '-f', 'rawvideo', '-vcodec', 'rawvideo',
        '-s', f'{WIDTH}x{HEIGHT}', '-pix_fmt', 'rgb24',
        '-r', str(FPS),
        '-i', 'pipe:0',
        '-i', audio_clip,
        '-map', '0:v:0', '-map', '1:a:0',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
        '-c:a', 'aac', '-b:a', '192k',
        '-pix_fmt', 'yuv420p',
        '-shortest',
        output_file
    ]
    ffmpeg_proc = subprocess.Popen(ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

    for frame_idx in range(total_frames):
        bg_idx = frame_idx % bg_frame_count
        out = bg_frames[bg_idx].copy()

        # 워터마크 패치
        if patch is not None:
            out[VEO_Y:VEO_Y+VEO_H, VEO_X:VEO_X+VEO_W] = patch

        # 비주얼라이저 fade
        current_time = frame_idx / FPS
        vis_fade = 1.0
        if current_time < VIS_FADE_IN:
            vis_fade = current_time / VIS_FADE_IN
        elif current_time > clip_duration - VIS_FADE_OUT:
            vis_fade = (clip_duration - current_time) / VIS_FADE_OUT
        vis_fade = max(0.0, min(1.0, vis_fade))

        bar_h = all_bar_heights[frame_idx]
        bar_alpha = VIS_BAR_ALPHA * vis_fade

        if vis_fade > 0:
            max_bh = int(VIS_BAR_MIN_HEIGHT + np.max(bar_h) * (VIS_BAR_MAX_HEIGHT - VIS_BAR_MIN_HEIGHT))
            vis_y_top = max(0, vis_bar_y_bottom - max_bh)
            vrh = vis_bar_y_bottom - vis_y_top
            vrw = vis_x_end - vis_x_start
            vis_region = out[vis_y_top:vis_bar_y_bottom, vis_x_start:vis_x_end].astype(np.float32) / 255.0

            # 그레인 생성/캐싱
            grain_key = (vrh, vrw)
            if grain_key not in cached_grain or frame_idx % GRAIN_INTERVAL == 0:
                cached_grain[grain_key] = make_grain(vrh, vrw)
            vis_grain = cached_grain[grain_key]

            for i in range(VIS_NUM_BARS):
                bh = int(VIS_BAR_MIN_HEIGHT + bar_h[i] * (VIS_BAR_MAX_HEIGHT - VIS_BAR_MIN_HEIGHT))
                local_x = vis_bar_positions[i] - vis_x_start
                local_y_top = vis_bar_y_bottom - vis_y_top - bh
                local_y_bot = vis_bar_y_bottom - vis_y_top
                if local_y_top < 0:
                    local_y_top = 0

                corners = {}
                half_alpha = bar_alpha * 0.5
                if local_y_bot - local_y_top >= 2 and VIS_BAR_WIDTH >= 2:
                    for dy in range(2):
                        for cx in [local_x, local_x + VIS_BAR_WIDTH - 1]:
                            cy = local_y_top + dy
                            if 0 <= cy < vrh and 0 <= cx < vrw:
                                corners[(cy, cx)] = vis_region[cy, cx].copy()

                bar_grain = vis_grain[local_y_top:local_y_bot, local_x:local_x + VIS_BAR_WIDTH]
                bar_with_grain = (bar_color_f + bar_grain * GRAIN_STRENGTH).clip(0, 1)

                region = vis_region[local_y_top:local_y_bot, local_x:local_x + VIS_BAR_WIDTH]
                vis_region[local_y_top:local_y_bot, local_x:local_x + VIS_BAR_WIDTH] = region * (1 - bar_alpha) + bar_with_grain * bar_alpha

                for (cy, cx), orig in corners.items():
                    vis_region[cy, cx] = orig * (1 - half_alpha) + bar_color_f * half_alpha

            out[vis_y_top:vis_bar_y_bottom, vis_x_start:vis_x_end] = (vis_region * 255).clip(0, 255).astype(np.uint8)

        ffmpeg_proc.stdin.write(out.tobytes())

        if (frame_idx + 1) % (FPS * 10) == 0 or frame_idx == total_frames - 1:
            pct = (frame_idx + 1) / total_frames * 100
            print(f"  📊 {pct:.1f}%")
            sys.stdout.flush()

    ffmpeg_proc.stdin.close()
    ffmpeg_proc.communicate()

    shutil.rmtree(temp_dir)

    if not os.path.exists(output_file):
        print("❌ 영상 생성 실패")
        return

    size = os.path.getsize(output_file) / (1024 * 1024)
    print(f"\n========================================")
    print(f"✅ 쇼츠 생성 완료!")
    print(f"📁 출력: {output_file}")
    print(f"📦 파일 크기: {size:.1f}MB")
    print(f"⏱  길이: {clip_duration:.1f}초")
    print("========================================")


if __name__ == '__main__':
    main()
