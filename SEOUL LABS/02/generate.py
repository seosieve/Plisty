#!/usr/bin/env python3
"""
SEOUL LABS Playlist Video Generator (Python 통합 버전)
배경 이미지 + 파티클 + 오디오 바 비주얼라이저 + 가사 싱크 + 텍스트 오버레이
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import subprocess
import os
import sys
import json
import random
import math
import wave
import re
import tempfile
import shutil

# ============================================================
# 설정
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SONGS_DIR = os.path.join(SCRIPT_DIR, "songs")
IMAGES_DIR = os.path.join(SCRIPT_DIR, "images")
LYRICS_DIR = os.path.join(SCRIPT_DIR, "lyrics")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
TRACKLIST = os.path.join(SCRIPT_DIR, "tracklist.json")

OUTPUT_FILE = os.path.join(OUTPUT_DIR, "playlist_output.mp4")

# 테스트 모드 (0 = 전체 렌더, 양수 = 해당 초만 렌더)
TEST_DURATION = 0

# 배경 이미지 (1장 고정)
BG_IMAGE = os.path.join(IMAGES_DIR, "SnowFlake_Extend.png")

# 영상 설정
WIDTH = 1920
HEIGHT = 1080
FPS = 30

# 바 비주얼라이저 설정
NUM_BARS = 48
BAR_WIDTH = 2
BAR_GAP = 4
BAR_MAX_HEIGHT = 70
BAR_MIN_HEIGHT = 2
BAR_ALPHA = 204  # 80% 투명도
BAR_Y_CENTER = HEIGHT - 180
SMOOTHING = 0.3

# 텍스트 오버레이 설정
FONT_PATH = os.path.expanduser("~/Library/Fonts/Roboto-LightItalic.ttf")
TEXT_FONT_SIZE = 38
TEXT_COLOR = "0xEEEEEE"
TEXT_X = "(w-text_w)/2"
TEXT_Y = "h-150"
TEXT_FADE_IN = 1
TEXT_FADE_OUT = 1

# 가사 오버레이 설정
LYRICS_FONT_PATH = os.path.expanduser("~/Library/Fonts/MapoFlowerIsland.otf")
LYRICS_FONT_SIZE = 32
LYRICS_Y = "h-90"
LYRICS_FADE = 0.2

# 파티클 설정
NUM_PARTICLES = 80
MIN_SIZE = 1
MAX_SIZE = 8
MIN_SPEED = 0.3
MAX_SPEED = 1.5
MIN_ALPHA = 30
MAX_ALPHA = 100
DRIFT_AMP = 1.5
DRIFT_FREQ_MIN = 0.02
DRIFT_FREQ_MAX = 0.06
PARTICLE_COLOR = (238, 238, 238)
BLUR_RADIUS = 1.5


# ============================================================
# 파티클 클래스
# ============================================================
class Particle:
    def __init__(self):
        self.reset(initial=True)

    def reset(self, initial=False):
        self.x = random.uniform(0, WIDTH)
        self.y = random.uniform(-HEIGHT, 0) if not initial else random.uniform(0, HEIGHT)
        self.size = random.uniform(MIN_SIZE, MAX_SIZE)
        self.speed = random.uniform(MIN_SPEED, MAX_SPEED)
        self.alpha = random.randint(MIN_ALPHA, MAX_ALPHA)
        self.drift_freq = random.uniform(DRIFT_FREQ_MIN, DRIFT_FREQ_MAX)
        self.drift_amp = random.uniform(DRIFT_AMP * 0.5, DRIFT_AMP)
        self.drift_phase = random.uniform(0, math.pi * 2)
        self.frame = 0
        depth = (self.speed - MIN_SPEED) / (MAX_SPEED - MIN_SPEED)
        self.size *= 0.5 + depth * 0.5
        self.alpha = int(self.alpha * (0.4 + depth * 0.6))

    def update(self):
        self.frame += 1
        self.y += self.speed
        self.x += math.sin(self.frame * self.drift_freq + self.drift_phase) * self.drift_amp
        if self.y > HEIGHT + 10:
            self.reset(initial=False)
        if self.x < -10:
            self.x = WIDTH + 10
        elif self.x > WIDTH + 10:
            self.x = -10


# ============================================================
# 유틸리티
# ============================================================
def load_image_aspect_fill(path):
    """이미지를 aspect fill로 로드 (비율 유지, 꽉 채우고 중앙 크롭)"""
    img = Image.open(path).convert('RGB')
    src_w, src_h = img.size
    scale = max(WIDTH / src_w, HEIGHT / src_h)
    scaled_w = int(src_w * scale)
    scaled_h = int(src_h * scale)
    img = img.resize((scaled_w, scaled_h), Image.LANCZOS)
    left = (scaled_w - WIDTH) // 2
    top = (scaled_h - HEIGHT) // 2
    img = img.crop((left, top, left + WIDTH, top + HEIGHT))
    return img.convert('RGBA')


def get_duration(filepath):
    """ffprobe로 오디오 길이 반환"""
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', filepath],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def get_audio_data(filepath, temp_dir):
    """오디오를 WAV로 변환 후 numpy 배열로 로드 (모노, 44100Hz)"""
    wav_path = os.path.join(temp_dir, os.path.basename(filepath) + '.mono.wav')
    subprocess.run([
        'ffmpeg', '-y', '-i', filepath, '-map', '0:a:0',
        '-ar', '44100', '-ac', '1', '-c:a', 'pcm_s16le', wav_path
    ], capture_output=True)

    with wave.open(wav_path, 'rb') as wf:
        raw = wf.readframes(wf.getnframes())
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

    max_val = np.max(np.abs(samples))
    if max_val > 0:
        samples = samples / max_val
    return samples, 44100


def precompute_bar_heights(all_samples, sample_rate, total_frames):
    """주파수 분석 + 볼륨 연동으로 바 높이 계산"""
    samples_per_frame = sample_rate // FPS
    all_heights = np.zeros((total_frames, NUM_BARS), dtype=np.float32)

    for frame_idx in range(total_frames):
        start = frame_idx * samples_per_frame
        end = start + samples_per_frame * 2

        if start >= len(all_samples):
            break

        end = min(end, len(all_samples))
        chunk = all_samples[start:end]

        if len(chunk) < 256:
            continue

        volume = np.sqrt(np.mean(chunk ** 2))

        window = np.hanning(len(chunk))
        fft = np.abs(np.fft.rfft(chunk * window))
        fft = fft[:len(fft) // 2]

        if len(fft) == 0:
            continue

        usable_range = len(fft) // 10
        freq_bins = np.linspace(1, usable_range, NUM_BARS + 1, dtype=int)
        freq_bins = np.unique(np.clip(freq_bins, 0, len(fft) - 1))

        heights = []
        for i in range(min(len(freq_bins) - 1, NUM_BARS)):
            s, e = freq_bins[i], freq_bins[i + 1]
            if s == e:
                e = s + 1
            heights.append(np.mean(fft[s:e]))

        while len(heights) < NUM_BARS:
            heights.append(0)

        h = np.array(heights[:NUM_BARS])
        max_val = np.max(h)
        if max_val > 0:
            h = h / max_val

        all_heights[frame_idx] = h * volume

    max_val = np.max(all_heights)
    if max_val > 0:
        all_heights = all_heights / max_val

    all_heights = np.power(all_heights, 0.7)

    smoothed = np.zeros_like(all_heights)
    prev = np.zeros(NUM_BARS)
    for i in range(total_frames):
        prev = prev * (1 - SMOOTHING) + all_heights[i] * SMOOTHING
        smoothed[i] = prev

    return smoothed


def parse_lyrics_json(json_path):
    """JSON에서 라인별 (start_s, end_s, text) 리스트 반환"""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    words = data['aligned_words']
    lines = []
    current_line = ""
    current_start = None
    current_end = None

    for w in words:
        word = w['word']
        start = w['start_s']
        end = w['end_s']

        word = re.sub(r'\[.*?\]', '', word, flags=re.DOTALL)

        if current_start is None:
            current_start = start

        if word.endswith('\n'):
            current_line += word.rstrip('\n')
            current_end = end
            text = current_line.strip()
            # 대괄호 태그만 있던 줄이나 빈 줄 스킵
            if text and not re.match(r'^\[.*\]$', text, re.DOTALL):
                lines.append((current_start, current_end, text))
            current_line = ""
            current_start = None
            current_end = None
        else:
            current_line += word
            current_end = end

    if current_line.strip() and current_start is not None:
        lines.append((current_start, current_end, current_line.strip()))

    # 짧은 줄 합치기 (3단어 이하면 다음 줄과 병합)
    merged = []
    i = 0
    while i < len(lines):
        start_s, end_s, text = lines[i]
        word_count = len(text.split())
        # 현재 줄이 3단어 이하이고 다음 줄도 있으면 병합
        if word_count <= 3 and i + 1 < len(lines):
            next_start, next_end, next_text = lines[i + 1]
            next_word_count = len(next_text.split())
            # 다음 줄도 짧으면 합치기 (합쳐서 8단어 이하일 때만)
            if next_word_count <= 3 and word_count + next_word_count <= 8:
                merged.append((start_s, next_end, f"{text} {next_text}"))
                i += 2
                continue
        merged.append((start_s, end_s, text))
        i += 1

    # end_s 여유 추가 (다음 줄 시작을 넘지 않도록)
    LYRICS_EXTEND = 0.8
    for i in range(len(merged)):
        start_s, end_s, text = merged[i]
        extended_end = end_s + LYRICS_EXTEND
        if i + 1 < len(merged):
            next_start = merged[i + 1][0]
            extended_end = min(extended_end, next_start)
        merged[i] = (start_s, extended_end, text)

    return merged


def render_particles(particles, particle_layer):
    """파티클을 레이어에 렌더링"""
    for p in particles:
        p.update()
        x, y, s = int(p.x), int(p.y), p.size
        if -20 <= y <= HEIGHT + 20 and -20 <= x <= WIDTH + 20:
            blur_r = BLUR_RADIUS * (s / MIN_SIZE) * 0.4
            margin = int(s + blur_r * 3) + 2
            patch_size = margin * 2 + 1
            ps = Image.new('RGBA', (patch_size, patch_size), (0, 0, 0, 0))
            pd = ImageDraw.Draw(ps)
            cx, cy = margin, margin
            si = int(round(s))
            pd.ellipse(
                [cx - si, cy - si, cx + si, cy + si],
                fill=(*PARTICLE_COLOR, p.alpha)
            )
            ps = ps.filter(ImageFilter.GaussianBlur(radius=blur_r))
            px, py = x - margin, y - margin
            particle_layer.alpha_composite(ps, (max(0, px), max(0, py)))
    return particle_layer


def render_bars(draw, bar_heights, bar_positions, total_bars, static_bars):
    """비주얼라이저 바를 레이어에 렌더링"""
    for i in range(total_bars):
        x = bar_positions[i]

        if i < static_bars or i >= static_bars + NUM_BARS:
            h = BAR_MIN_HEIGHT
        else:
            h = int(BAR_MIN_HEIGHT + bar_heights[i - static_bars] * (BAR_MAX_HEIGHT - BAR_MIN_HEIGHT))

        y_top = BAR_Y_CENTER - h // 2
        y_bottom = BAR_Y_CENTER + h // 2
        y_top = max(0, y_top)
        y_bottom = min(HEIGHT, y_bottom)

        draw.rectangle([x, y_top, x + BAR_WIDTH, y_bottom], fill=(238, 238, 238, BAR_ALPHA))

        if y_bottom - y_top >= 2 and BAR_WIDTH >= 2:
            half_alpha = BAR_ALPHA // 2
            for cx, cy in [(x, y_top), (x + BAR_WIDTH, y_top),
                           (x, y_bottom), (x + BAR_WIDTH, y_bottom)]:
                if 0 <= cx < WIDTH and 0 <= cy < HEIGHT:
                    draw.point((cx, cy), fill=(238, 238, 238, half_alpha))


# ============================================================
# 메인
# ============================================================
def main():
    print("🎬 SEOUL LABS Playlist Video Generator")
    print("========================================")

    # tracklist 로드
    with open(TRACKLIST, 'r', encoding='utf-8') as f:
        tracklist = json.load(f)

    track_count = len(tracklist)
    print(f"📋 트랙 수: {track_count}")

    # 배경 이미지 로드
    if not os.path.exists(BG_IMAGE):
        print(f"❌ 배경 이미지를 찾을 수 없습니다: {BG_IMAGE}")
        return

    print(f"🖼  배경: {os.path.basename(BG_IMAGE)}")
    bg_image = load_image_aspect_fill(BG_IMAGE)

    temp_dir = tempfile.mkdtemp(prefix='seoullabs_render_')

    # 오디오 분석 (비주얼라이저용)
    print("\n🔊 오디오 분석 중...")
    all_samples = np.array([], dtype=np.float32)
    sample_rate = 44100
    track_starts = []
    track_durations = []

    for track in tracklist:
        filepath = os.path.join(SONGS_DIR, track['file'])
        if not os.path.exists(filepath):
            print(f"  ❌ 음악 파일 없음: {track['file']}")
            return
        samples, sr = get_audio_data(filepath, temp_dir)
        track_starts.append(len(all_samples) / sample_rate)
        track_durations.append(len(samples) / sample_rate)
        all_samples = np.concatenate([all_samples, samples])
        print(f"  ✅ {track['title']}")

    total_duration = len(all_samples) / sample_rate
    if TEST_DURATION > 0:
        total_duration = min(total_duration, TEST_DURATION)
        OUTPUT_FILE_USED = os.path.join(OUTPUT_DIR, f"playlist_test_{TEST_DURATION}s.mp4")
    else:
        OUTPUT_FILE_USED = OUTPUT_FILE
    total_frames = int(total_duration * FPS)
    total_mins = int(total_duration // 60)
    total_secs = int(total_duration % 60)

    for i, track in enumerate(tracklist):
        mins = int(track_starts[i] // 60)
        secs = int(track_starts[i] % 60)
        dur_mins = int(track_durations[i] // 60)
        dur_secs = int(track_durations[i] % 60)
        print(f"  [{i+1}] {track['title']} - {track['artist']}  ({dur_mins}:{dur_secs:02d})  @ {mins}:{secs:02d}")

    print(f"\n⏱  총 재생시간: {total_mins}:{total_secs:02d}")
    print(f"🎞  총 프레임: {total_frames}")

    # 바 높이 미리 계산
    print("\n📊 주파수 분석 중...")
    all_bar_heights = precompute_bar_heights(all_samples, sample_rate, total_frames)
    print("  ✅ 분석 완료")

    # 바 위치 미리 계산
    STATIC_BARS = 3
    total_bars = STATIC_BARS + NUM_BARS + STATIC_BARS
    total_bar_width = total_bars * BAR_WIDTH + (total_bars - 1) * BAR_GAP
    start_x = (WIDTH - total_bar_width) // 2
    bar_positions = [start_x + i * (BAR_WIDTH + BAR_GAP) for i in range(total_bars)]

    # 오디오 합치기
    print("\n🎵 오디오 합치는 중...")
    concat_list = os.path.join(temp_dir, 'audio_list.txt')
    with open(concat_list, 'w') as cl:
        for i, track in enumerate(tracklist):
            wav_file = os.path.join(temp_dir, f'track_{i}.wav')
            subprocess.run([
                'ffmpeg', '-y', '-i', os.path.join(SONGS_DIR, track['file']),
                '-map', '0:a:0', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', wav_file
            ], capture_output=True)
            cl.write(f"file '{wav_file}'\n")

    merged_audio = os.path.join(temp_dir, 'merged_audio.wav')
    subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', concat_list, '-c', 'copy', merged_audio
    ], capture_output=True)
    print("✅ 오디오 합치기 완료")

    # drawtext 필터 생성 (곡 제목 + 가사)
    print("\n✍️  텍스트 오버레이 준비 중...")
    drawtext_filters = []

    # 곡 제목
    for i, track in enumerate(tracklist):
        title = track['title']
        start = track_starts[i]
        duration = track_durations[i]

        text_file = os.path.join(temp_dir, f'text_{i}.txt')
        with open(text_file, 'w', encoding='utf-8') as tf:
            tf.write(title)

        fade_in_start = start
        fade_in_end = start + TEXT_FADE_IN
        fade_out_start = start + duration - 1 - TEXT_FADE_OUT
        fade_out_end = start + duration - 1

        alpha = (
            f"if(lt(t\\,{fade_in_start})\\,0\\,"
            f" if(lt(t\\,{fade_in_end})\\,(t-{fade_in_start})/{TEXT_FADE_IN}\\,"
            f" if(lt(t\\,{fade_out_start})\\,1\\,"
            f" if(lt(t\\,{fade_out_end})\\,1-(t-{fade_out_start})/{TEXT_FADE_OUT}\\,"
            f" 0))))"
        )

        drawtext_filters.append(
            f"drawtext=fontfile='{FONT_PATH}'"
            f":textfile='{text_file}'"
            f":fontsize={TEXT_FONT_SIZE}:fontcolor={TEXT_COLOR}"
            f":borderw=1:bordercolor=0x373737"
            f":x={TEXT_X}:y={TEXT_Y}"
            f":alpha='{alpha}'"
        )

    # 가사 싱크 오버레이
    for i, track in enumerate(tracklist):
        name = os.path.splitext(track['file'])[0]
        json_path = os.path.join(LYRICS_DIR, f"{name}.json")
        if not os.path.exists(json_path):
            print(f"  ⚠️  가사 없음: {name}")
            continue

        lyrics = parse_lyrics_json(json_path)
        track_offset = track_starts[i]
        print(f"  🎤 {track['title']}: {len(lyrics)}줄 가사 로드")

        for j, (start_s, end_s, text) in enumerate(lyrics):
            abs_start = track_offset + start_s
            abs_end = track_offset + end_s

            fade_in_s = abs_start
            fade_in_e = abs_start + LYRICS_FADE
            fade_out_s = abs_end - LYRICS_FADE
            fade_out_e = abs_end

            alpha = (
                f"if(lt(t\\,{fade_in_s})\\,0\\,"
                f" if(lt(t\\,{fade_in_e})\\,0.8*(t-{fade_in_s})/{LYRICS_FADE}\\,"
                f" if(lt(t\\,{fade_out_s})\\,0.8\\,"
                f" if(lt(t\\,{fade_out_e})\\,0.8-0.8*(t-{fade_out_s})/{LYRICS_FADE}\\,"
                f" 0))))"
            )

            lyric_text_file = os.path.join(temp_dir, f'lyric_{i}_{j}.txt')
            with open(lyric_text_file, 'w', encoding='utf-8') as lf:
                lf.write(f"♪ {text}")

            drawtext_filters.append(
                f"drawtext=fontfile='{LYRICS_FONT_PATH}'"
                f":textfile='{lyric_text_file}'"
                f":fontsize={LYRICS_FONT_SIZE}:fontcolor=0xEEEEEE"
                f":borderw=1:bordercolor=0x373737"
                f":x=(w-text_w)/2:y={LYRICS_Y}"
                f":alpha='{alpha}'"
            )

    vf_filter = ','.join(drawtext_filters)

    # 파티클 초기화
    particles = [Particle() for _ in range(NUM_PARTICLES)]
    print(f"✨ 파티클: {NUM_PARTICLES}개")

    # FFmpeg 파이프 시작
    print("\n🎬 영상 생성 중...")
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    ffmpeg_cmd = [
        'ffmpeg', '-y',
        '-f', 'rawvideo', '-vcodec', 'rawvideo',
        '-s', f'{WIDTH}x{HEIGHT}', '-pix_fmt', 'rgb24',
        '-r', str(FPS),
        '-i', '-',
        '-i', merged_audio,
        '-vf', vf_filter,
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
        '-c:a', 'aac', '-b:a', '192k',
        '-pix_fmt', 'yuv420p',
        '-shortest',
        OUTPUT_FILE_USED
    ]

    ffmpeg_proc = subprocess.Popen(
        ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE
    )

    # 프레임 렌더링 루프
    for frame_idx in range(total_frames):
        frame = bg_image.copy()

        # 바 비주얼라이저 레이어
        bar_layer = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
        draw = ImageDraw.Draw(bar_layer)
        render_bars(draw, all_bar_heights[frame_idx], bar_positions, total_bars, STATIC_BARS)
        frame = Image.alpha_composite(frame, bar_layer)

        # 파티클 레이어
        particle_layer = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
        particle_layer = render_particles(particles, particle_layer)
        frame = Image.alpha_composite(frame, particle_layer)

        # RGB로 변환하여 전송
        frame_rgb = frame.convert('RGB')
        ffmpeg_proc.stdin.write(np.array(frame_rgb).tobytes())

        # 진행도 표시 (30초마다)
        if (frame_idx + 1) % (FPS * 30) == 0 or frame_idx == total_frames - 1:
            pct = (frame_idx + 1) / total_frames * 100
            elapsed_min = int((frame_idx / FPS) // 60)
            elapsed_sec = int((frame_idx / FPS) % 60)
            print(f"  📊 {pct:.1f}% ({elapsed_min}:{elapsed_sec:02d}/{total_mins}:{total_secs:02d})")
            sys.stdout.flush()

    ffmpeg_proc.stdin.close()
    _, stderr = ffmpeg_proc.communicate()

    if ffmpeg_proc.returncode != 0 or not os.path.exists(OUTPUT_FILE_USED):
        print("❌ 영상 생성 실패. 로그:")
        log_path = os.path.join(temp_dir, 'ffmpeg_final.log')
        with open(log_path, 'wb') as lf:
            lf.write(stderr)
        print(stderr.decode(errors='replace')[-2000:])
        return

    # 정리
    shutil.rmtree(temp_dir)

    size = os.path.getsize(OUTPUT_FILE_USED) / (1024 * 1024)
    print("")
    print("========================================")
    print("✅ 영상 생성 완료!")
    print(f"📁 출력: {OUTPUT_FILE_USED}")
    print(f"📦 파일 크기: {size:.0f}MB")
    print(f"⏱  총 재생시간: {total_mins}:{total_secs:02d}")
    print("========================================")


if __name__ == '__main__':
    main()
