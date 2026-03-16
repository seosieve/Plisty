#!/usr/bin/env python3
"""
Mumyung Playlist Video Generator (Python 통합 버전)
이미지 전환 (cross dissolve) + 파티클 + 텍스트 오버레이 + 오디오
"""

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import subprocess
import os
import sys
import json
import random
import math
import tempfile
import shutil

# ============================================================
# 설정
# ============================================================
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SONGS_DIR = os.path.join(SCRIPT_DIR, "songs")
IMAGES_DIR = os.path.join(SCRIPT_DIR, "images")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
TRACKLIST = os.path.join(SCRIPT_DIR, "tracklist.json")

OUTPUT_FILE = os.path.join(OUTPUT_DIR, "playlist_output.mp4")

# 테스트 모드 (0 = 전체 렌더, 양수 = 해당 초만 렌더)
TEST_DURATION = 0

# 영상 설정
WIDTH = 1920
HEIGHT = 1080
FPS = 30

# 이미지 전환 설정
XFADE_DUR = 2  # cross dissolve 시간 (초)

# 텍스트 오버레이 설정 (ffmpeg drawtext 사용)
FONT_PATH = os.path.expanduser("~/Library/Fonts/MapoFlowerIsland.otf")
TEXT_FONT_SIZE = 38
TEXT_COLOR = "0xEEEEEE"
TEXT_X = "(w-text_w)/2"
TEXT_Y = "h-120"
TEXT_FADE_IN = 1
TEXT_FADE_OUT = 1

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

IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp')


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


def find_image_for_title(title):
    """곡 제목과 동일한 이미지 파일 탐색"""
    for ext in IMAGE_EXTS:
        path = os.path.join(IMAGES_DIR, f"{title}{ext}")
        if os.path.exists(path):
            return path
    return None


def get_duration(filepath):
    """ffprobe로 오디오 길이 반환"""
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', filepath],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def blend_images(img_a, img_b, alpha):
    """두 이미지를 alpha 비율로 블렌딩 (0.0=A, 1.0=B)"""
    return Image.blend(img_a, img_b, alpha)


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


# ============================================================
# 메인
# ============================================================
def main():
    print("🎬 Mumyung Playlist Video Generator")
    print("========================================")

    # tracklist 로드
    with open(TRACKLIST, 'r', encoding='utf-8') as f:
        tracklist = json.load(f)

    track_count = len(tracklist)
    print(f"📋 트랙 수: {track_count}")

    # 이미지 매칭 & 로드
    print("🖼  이미지 로드 중...")
    track_images = []
    for track in tracklist:
        img_path = find_image_for_title(track['title'])
        if img_path:
            track_images.append(load_image_aspect_fill(img_path))
            print(f"  ✅ {track['title']}")
        else:
            print(f"  ❌ 이미지 없음: {track['title']}")
            return

    # 곡 duration 계산
    print("\n📊 트랙 정보 분석 중...")
    track_starts = []
    track_durations = []
    current_time = 0.0

    for track in tracklist:
        filepath = os.path.join(SONGS_DIR, track['file'])
        if not os.path.exists(filepath):
            print(f"  ❌ 음악 파일 없음: {track['file']}")
            return
        duration = get_duration(filepath)
        track_starts.append(current_time)
        track_durations.append(duration)

        mins = int(current_time // 60)
        secs = int(current_time % 60)
        dur_mins = int(duration // 60)
        dur_secs = int(duration % 60)
        idx = len(track_starts)
        print(f"  [{idx}] {track['title']} - {track['artist']}  ({dur_mins}:{dur_secs:02d})  @ {mins}:{secs:02d}")

        current_time += duration

    total_duration = current_time
    if TEST_DURATION > 0:
        total_duration = min(total_duration, TEST_DURATION)
        OUTPUT_FILE_USED = os.path.join(OUTPUT_DIR, f"playlist_test_{TEST_DURATION}s.mp4")
    else:
        OUTPUT_FILE_USED = OUTPUT_FILE
    total_frames = int(total_duration * FPS)
    total_mins = int(total_duration // 60)
    total_secs = int(total_duration % 60)
    print(f"\n⏱  총 재생시간: {total_mins}:{total_secs:02d}")
    print(f"🎞  총 프레임: {total_frames}")

    # 오디오 합치기
    print("\n🎵 오디오 합치는 중...")
    temp_dir = tempfile.mkdtemp(prefix='mumyung_render_')

    concat_list = os.path.join(temp_dir, 'audio_list.txt')
    with open(concat_list, 'w') as cl:
        for i, track in enumerate(tracklist):
            wav_file = os.path.join(temp_dir, f'track_{i}.wav')
            subprocess.run([
                'ffmpeg', '-y', '-i', os.path.join(SONGS_DIR, track['file']),
                '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le', wav_file
            ], capture_output=True)
            cl.write(f"file '{wav_file}'\n")

    merged_audio = os.path.join(temp_dir, 'merged_audio.wav')
    subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', concat_list, '-c', 'copy', merged_audio
    ], capture_output=True)
    print("✅ 오디오 합치기 완료")

    # drawtext 필터 생성
    print("\n✍️  텍스트 오버레이 준비 중...")
    drawtext_filters = []

    for i, track in enumerate(tracklist):
        title = f"{i+1:02d}. {track['title']}"
        start = track_starts[i]
        duration = track_durations[i]

        # 텍스트 파일로 저장 (한글 이스케이프 안전)
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
        t = frame_idx / FPS  # 현재 시간 (초)

        # 현재 곡 인덱스 찾기
        track_idx = 0
        for i in range(track_count):
            if t >= track_starts[i]:
                track_idx = i

        # 배경 이미지 결정 (cross dissolve 처리)
        current_img = track_images[track_idx]

        if track_idx < track_count - 1:
            # 다음 곡 전환 시점 체크
            next_start = track_starts[track_idx + 1]
            xfade_start = next_start - XFADE_DUR

            if t >= xfade_start and t < next_start:
                # cross dissolve 구간
                progress = (t - xfade_start) / XFADE_DUR
                next_img = track_images[track_idx + 1]
                frame = blend_images(current_img, next_img, progress)
            else:
                frame = current_img.copy()
        else:
            frame = current_img.copy()

        # 파티클 레이어
        particle_layer = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
        particle_layer = render_particles(particles, particle_layer)

        # 합성
        frame = Image.alpha_composite(frame, particle_layer)
        frame_rgb = frame.convert('RGB')

        # ffmpeg에 전달
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
