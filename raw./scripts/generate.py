#!/usr/bin/env python3
"""
raw. Playlist Video Generator
루프 영상 배경 + 곡 번호 텍스트 (PIL) + 노래 순차 재생 (곡 전환 시 볼륨 페이드)
"""

import os
import re
import shutil
import subprocess
import sys
import tempfile

# caffeinate 자동 적용 (잠자기 방지)
if not os.environ.get("CAFFEINATED"):
    os.environ["CAFFEINATED"] = "1"
    os.execvp("caffeinate", ["caffeinate", "-dims", sys.executable] + sys.argv)

import numpy as np
from PIL import Image, ImageDraw, ImageFont


GRAIN_STRENGTH = 0.15  # 노이즈 강도 (0=없음, 1=풀 노이즈)
GRAIN_SIZE = 2  # 노이즈 입자 크기 (1=미세, 커질수록 거친 느낌)
GRAIN_COLOR = 0.5  # 컬러 노이즈 비율 (0=흑백, 1=풀컬러)
GRAIN_INTERVAL = 15  # 그레인 변경 주기 (프레임 수, 15=초당 2회)

# ============================================================
# 설정
# ============================================================
args = sys.argv[1:]

if not args:
    print("사용법: python3 generate.py <EP 폴더 경로> [곡 수 제한]")
    print("예: python3 generate.py ../EP01_260401")
    print("    python3 generate.py ../EP01_260401 3  # 1~3곡만")
    sys.exit(1)

EP_DIR = os.path.abspath(args[0])
SONGS_DIR = os.path.join(EP_DIR, "songs")
LOOPS_DIR = os.path.join(EP_DIR, "loops")
OUTPUT_DIR = os.path.join(EP_DIR, "outputs")

EP_NAME = os.path.basename(EP_DIR)
OUTPUT_FILE = os.path.join(OUTPUT_DIR, f"{EP_NAME}.mp4")

AUDIO_EXTENSIONS = {'.wav', '.mp3', '.flac', '.m4a', '.aac'}
FPS = 30
SCALE = 2  # 1 = 1080p, 2 = 4K

# 볼륨 페이드 설정 (초)
FADE_IN = 0.5
FADE_OUT = 1.0

# 곡 번호 텍스트 설정
NUM_FONT_PATH = os.path.expanduser("~/Library/Fonts/DelaGothicOne-Regular.ttf")
NUM_FONT_SIZE = 100 * SCALE
NUM_COLOR = (254, 255, 245)  # #FEFFF5
NUM_ALPHA = 0.7
TEXT_X = 1480 * SCALE
TEXT_MARGIN_TOP = 60 * SCALE
TEXT_FADE_IN = 1.0
TEXT_FADE_OUT = 1.0
TEXT_DELAY = 1.0  # 비주얼라이저 대비 텍스트 지연 (초)

# 곡 제목 텍스트 설정
TITLE_FONT_PATH = os.path.expanduser("~/Library/Fonts/PartialSansKR-Regular.otf")
TITLE_FONT_SIZE = 50 * SCALE
TITLE_COLOR = (254, 255, 245)  # #FEFFF5
TITLE_ALPHA = 0.7
TITLE_GAP = 80 * SCALE  # 곡 번호와 제목 사이 간격
TITLE_MAX_WIDTH = 280 * SCALE  # 이 너비 초과 시 줄바꿈

# 부제 텍스트 설정 (하단)
SUB_FONT_PATH = os.path.expanduser("~/Library/Fonts/AlumniSans-Italic[wght].ttf")
SUB_FONT_SIZE = 56 * SCALE
SUB_FONT_WEIGHT = 800  # Extrabold
SUB_COLOR = (254, 255, 245)  # #FEFFF5
SUB_ALPHA = 0.5
SUB_MARGIN_BOTTOM = 120 * SCALE  # 하단 여백

# 바 비주얼라이저 설정
VIS_NUM_BARS = 32
VIS_BAR_WIDTH = 4 * SCALE
VIS_BAR_GAP = 4 * SCALE
VIS_BAR_MAX_HEIGHT = 80 * SCALE
VIS_BAR_MIN_HEIGHT = 2 * SCALE
VIS_BAR_ALPHA = 0.5
VIS_BAR_COLOR = (254, 255, 245)  # #FEFFF5
VIS_MARGIN_BOTTOM = 152 * SCALE  # 바 아래쪽(고정) 위치 = 부제 위
VIS_SMOOTHING = 0.3


# ============================================================
# 유틸리티
# ============================================================
def precompute_bar_heights(audio_path, total_frames):
    """오디오에서 프레임별 바 높이 계산"""
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

    # 스무딩
    smoothed = np.zeros_like(all_heights)
    prev = np.zeros(VIS_NUM_BARS)
    for i in range(total_frames):
        prev = prev * (1 - VIS_SMOOTHING) + all_heights[i] * VIS_SMOOTHING
        smoothed[i] = prev
    return smoothed


def make_grain(h, w):
    """그레인 노이즈 생성"""
    if GRAIN_SIZE > 1:
        mono = np.random.randn(h // GRAIN_SIZE + 1, w // GRAIN_SIZE + 1, 1).astype(np.float32)
        color = np.random.randn(h // GRAIN_SIZE + 1, w // GRAIN_SIZE + 1, 3).astype(np.float32)
        small = mono * (1 - GRAIN_COLOR) + color * GRAIN_COLOR
        return np.repeat(np.repeat(small, GRAIN_SIZE, axis=0), GRAIN_SIZE, axis=1)[:h, :w]
    else:
        mono = np.random.randn(h, w, 1).astype(np.float32)
        color = np.random.randn(h, w, 3).astype(np.float32)
        return mono * (1 - GRAIN_COLOR) + color * GRAIN_COLOR


def get_duration(filepath):
    """ffprobe로 오디오/영상 길이 반환"""
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', filepath],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def get_video_info(filepath):
    """ffprobe로 영상 해상도, FPS 반환"""
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
         '-show_entries', 'stream=width,height,r_frame_rate',
         '-of', 'csv=p=0', filepath],
        capture_output=True, text=True
    )
    parts = result.stdout.strip().split(',')
    width, height = int(parts[0]), int(parts[1])
    fps_parts = parts[2].split('/')
    fps = int(fps_parts[0]) / int(fps_parts[1]) if len(fps_parts) == 2 else float(fps_parts[0])
    return width, height, fps


def load_loop_frames(filepath):
    """루프 영상의 모든 프레임을 PIL Image 리스트로 로드 (SCALE 적용)"""
    src_w, src_h, _ = get_video_info(filepath)
    out_w, out_h = src_w * SCALE, src_h * SCALE

    if SCALE == 1:
        scale_filter = []
    else:
        scale_filter = ['-vf', f'scale={out_w}:{out_h}:flags=lanczos']

    result = subprocess.run([
        'ffmpeg', '-i', filepath,
        *scale_filter,
        '-f', 'rawvideo', '-pix_fmt', 'rgb24',
        '-v', 'quiet', '-'
    ], capture_output=True)

    raw = result.stdout
    frame_size = out_w * out_h * 3
    num_frames = len(raw) // frame_size

    frames = []
    for i in range(num_frames):
        frame_data = raw[i * frame_size:(i + 1) * frame_size]
        frames.append(Image.frombytes('RGB', (out_w, out_h), frame_data))

    return frames, out_w, out_h


# ============================================================
# 메인
# ============================================================
def main():
    print("🎬 raw. Playlist Video Generator")
    print("========================================")

    # songs/ 폴더 스캔
    if not os.path.isdir(SONGS_DIR):
        print(f"❌ songs 폴더를 찾을 수 없습니다: {SONGS_DIR}")
        return

    song_files = sorted([
        f for f in os.listdir(SONGS_DIR)
        if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
    ])

    if not song_files:
        print(f"❌ songs 폴더에 오디오 파일이 없습니다: {SONGS_DIR}")
        return

    # 곡 수 제한
    track_limit = int(args[1]) if len(args) >= 2 and int(args[1]) > 0 else len(song_files)
    song_files = song_files[:track_limit]

    # 2회 반복 (플레이리스트를 2번 재생)
    song_files = song_files * 2
    print(f"🔄 2회 반복 → 총 {len(song_files)}트랙")

    # loops/ 폴더에서 루프 영상 찾기
    loop_file = None
    if os.path.isdir(LOOPS_DIR):
        loop_files = sorted(f for f in os.listdir(LOOPS_DIR) if f.lower().endswith(('.mp4', '.mov', '.webm')))
        if loop_files:
            loop_file = os.path.join(LOOPS_DIR, loop_files[0])

    if not loop_file:
        print(f"❌ loops 폴더에 영상 파일이 없습니다: {LOOPS_DIR}")
        return

    # 부제: EP 번호 + 루프 파일명
    ep_num = int(re.search(r'EP(\d+)', EP_NAME).group(1))
    loop_name = os.path.splitext(os.path.basename(loop_file))[0]
    SUB_TEXT = f"{ep_num} {loop_name}"

    print(f"📂 EP: {EP_NAME}")
    print(f"🔁 루프 영상: {os.path.basename(loop_file)}")
    print(f"📝 부제: {SUB_TEXT}")
    print(f"📋 트랙 수: {len(song_files)}")

    # 루프 프레임 로드
    print("\n🔁 루프 프레임 로드 중...")
    loop_frames, width, height = load_loop_frames(loop_file)
    print(f"  ✅ {len(loop_frames)}프레임 로드 ({width}x{height})")

    # 트랙 정보
    total_duration = 0
    track_durations = []
    track_starts = []
    track_nums = []
    for f in song_files:
        filepath = os.path.join(SONGS_DIR, f)
        dur = get_duration(filepath)
        track_starts.append(total_duration)
        track_durations.append(dur)
        total_duration += dur
        # 파일명에서 트랙 번호 추출
        track_num = int(os.path.splitext(f)[0].split('_')[0]) if f[0].isdigit() else len(track_nums) + 1
        track_nums.append(track_num)
        dur_m = int(dur // 60)
        dur_s = int(dur % 60)
        title = os.path.splitext(f)[0]
        print(f"  ✅ {title} ({dur_m}:{dur_s:02d})")

    total_frames = int(total_duration * FPS)
    total_m = int(total_duration // 60)
    total_s = int(total_duration % 60)
    print(f"\n⏱  총 재생시간: {total_m}:{total_s:02d}")
    print(f"🎞  총 프레임: {total_frames}")

    temp_dir = tempfile.mkdtemp(prefix='raw_render_')
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # 오디오 합치기 (곡별 페이드 인/아웃 적용)
    print("\n🎵 오디오 합치는 중...")
    concat_list = os.path.join(temp_dir, 'audio_list.txt')
    with open(concat_list, 'w') as cl:
        for i, f in enumerate(song_files):
            filepath = os.path.join(SONGS_DIR, f)
            wav_file = os.path.join(temp_dir, f'track_{i}.wav')
            dur = track_durations[i]
            subprocess.run([
                'ffmpeg', '-y', '-i', filepath,
                '-map', '0:a:0', '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le',
                '-af', f'afade=t=in:st=0:d={FADE_IN},afade=t=out:st={dur - FADE_OUT}:d={FADE_OUT}',
                wav_file
            ], capture_output=True)
            cl.write(f"file '{wav_file}'\n")

    merged_audio = os.path.join(temp_dir, 'merged_audio.wav')
    subprocess.run([
        'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
        '-i', concat_list, '-c', 'copy', merged_audio
    ], capture_output=True)
    print("✅ 오디오 합치기 완료")

    # 비주얼라이저 FFT 분석
    print("\n📊 비주얼라이저 분석 중...")
    all_bar_heights = precompute_bar_heights(merged_audio, total_frames)
    vis_bar_y_bottom = height - VIS_MARGIN_BOTTOM
    vis_bar_positions = [TEXT_X + i * (VIS_BAR_WIDTH + VIS_BAR_GAP) for i in range(VIS_NUM_BARS)]
    print("✅ 분석 완료")

    # 텍스트 준비
    print("\n✍️  텍스트 오버레이 준비 중...")
    num_font = ImageFont.truetype(NUM_FONT_PATH, NUM_FONT_SIZE)
    title_font = ImageFont.truetype(TITLE_FONT_PATH, TITLE_FONT_SIZE)

    def strip_track_prefix(name):
        return re.sub(r'^\d+_', '', name)

    def wrap_title(text, font, max_width):
        """제목이 max_width 초과 시 줄바꿈 (띄어쓰기 우선, 없으면 글자 단위, 재귀)"""
        bbox = font.getbbox(text)
        if bbox[2] - bbox[0] <= max_width:
            return text
        # 띄어쓰기 위치에서 줄바꿈 시도 (뒤에서부터)
        for i in range(len(text) - 1, 0, -1):
            if text[i] == ' ':
                line1 = text[:i]
                if font.getbbox(line1)[2] - font.getbbox(line1)[0] <= max_width:
                    return line1 + '\n' + wrap_title(text[i+1:], font, max_width)
        # 띄어쓰기가 없으면 글자 단위
        for i in range(len(text), 0, -1):
            line1 = text[:i]
            if font.getbbox(line1)[2] - font.getbbox(line1)[0] <= max_width:
                return line1 + '\n' + wrap_title(text[i:], font, max_width)
        return text

    # 곡 번호 + 제목 타이밍
    title_timed = []
    for i in range(len(song_files)):
        num_text = f"{track_nums[i]:02d}."
        title_text = wrap_title(strip_track_prefix(os.path.splitext(song_files[i])[0]), title_font, TITLE_MAX_WIDTH)
        start = track_starts[i]
        end = start + track_durations[i]
        title_timed.append((start, end, num_text, title_text))

    # ffmpeg 파이프 시작
    print("\n🎬 영상 생성 중...")
    ffmpeg_cmd = [
        'ffmpeg', '-y',
        '-f', 'rawvideo', '-vcodec', 'rawvideo',
        '-s', f'{width}x{height}', '-pix_fmt', 'rgb24',
        '-r', str(FPS),
        '-i', '-',
        '-i', merged_audio,
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
        '-c:a', 'aac', '-b:a', '192k',
        '-pix_fmt', 'yuv420p',
        '-shortest',
        OUTPUT_FILE
    ]

    ffmpeg_proc = subprocess.Popen(
        ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE
    )

    # 텍스트 레이어 트랙별 캐시 (바운딩 박스 영역만)
    print("\n✍️  텍스트 레이어 캐시 중...")
    text_cache = []
    for t_start, t_end, num_text, song_title in title_timed:
        text_layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
        text_draw = ImageDraw.Draw(text_layer)

        num_bbox = num_font.getbbox(num_text)
        num_th = num_bbox[3] - num_bbox[1]

        text_draw.text((TEXT_X, TEXT_MARGIN_TOP), num_text, font=num_font,
                       fill=(*NUM_COLOR, int(NUM_ALPHA * 255)))

        title_y = TEXT_MARGIN_TOP + num_th + TITLE_GAP
        text_draw.text((TEXT_X, title_y), song_title, font=title_font,
                       fill=(*TITLE_COLOR, int(TITLE_ALPHA * 255)), spacing=20 * SCALE)

        # 바운딩 박스 계산 (알파가 0이 아닌 영역)
        t_arr = np.array(text_layer, dtype=np.float32) / 255.0
        alpha_mask = t_arr[:, :, 3] > 0
        rows = np.any(alpha_mask, axis=1)
        cols = np.any(alpha_mask, axis=0)
        y1, y2 = np.where(rows)[0][[0, -1]]
        x1, x2 = np.where(cols)[0][[0, -1]]
        # 약간 여유
        y1 = max(0, y1 - 2)
        x1 = max(0, x1 - 2)
        y2 = min(height - 1, y2 + 2)
        x2 = min(width - 1, x2 + 2)

        crop_rgb = t_arr[y1:y2+1, x1:x2+1, :3].copy()
        crop_alpha = t_arr[y1:y2+1, x1:x2+1, 3:4].copy()
        text_cache.append((t_start, t_end, crop_rgb, crop_alpha, y1, y2, x1, x2))
        print(f"  ✅ {num_text} {song_title.split(chr(10))[0]} → 영역 {x2-x1+1}x{y2-y1+1}px")
    print(f"  ✅ {len(text_cache)}트랙 캐시 완료")

    # 부제 캐시
    sub_font = ImageFont.truetype(SUB_FONT_PATH, SUB_FONT_SIZE)
    sub_font.set_variation_by_axes([SUB_FONT_WEIGHT])
    sub_layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    sub_draw = ImageDraw.Draw(sub_layer)
    sub_bbox = sub_font.getbbox(SUB_TEXT)
    sub_y = height - SUB_MARGIN_BOTTOM - (sub_bbox[3] - sub_bbox[1])
    sub_draw.text((TEXT_X, sub_y), SUB_TEXT, font=sub_font,
                  fill=(*SUB_COLOR, int(SUB_ALPHA * 255)))
    sub_arr = np.array(sub_layer, dtype=np.float32) / 255.0
    sub_alpha_mask = sub_arr[:, :, 3] > 0
    s_rows = np.any(sub_alpha_mask, axis=1)
    s_cols = np.any(sub_alpha_mask, axis=0)
    sy1, sy2 = np.where(s_rows)[0][[0, -1]]
    sx1, sx2 = np.where(s_cols)[0][[0, -1]]
    sy1, sx1 = max(0, sy1 - 2), max(0, sx1 - 2)
    sy2, sx2 = min(height - 1, sy2 + 2), min(width - 1, sx2 + 2)
    sub_rgb = sub_arr[sy1:sy2+1, sx1:sx2+1, :3].copy()
    sub_a = sub_arr[sy1:sy2+1, sx1:sx2+1, 3:4].copy()
    print(f"  ✅ 부제: \"{SUB_TEXT}\"")

    # 루프 프레임 numpy 캐시 (uint8 유지 — float 변환은 영역만)
    print("🔁 루프 프레임 numpy 변환 중...")
    loop_np = [np.array(f, dtype=np.uint8) for f in loop_frames]
    del loop_frames
    print(f"  ✅ {len(loop_np)}프레임 변환 완료")

    cached_grain = {}
    bar_color_f = np.array(VIS_BAR_COLOR, dtype=np.float32) / 255.0

    # 비주얼라이저 전체 바운딩 박스 (일괄 처리용)
    vis_x_start = vis_bar_positions[0]
    vis_x_end = vis_bar_positions[-1] + VIS_BAR_WIDTH

    # 프레임 렌더링 루프
    num_loop_frames = len(loop_np)
    for frame_idx in range(total_frames):
        current_time = frame_idx / FPS

        # 루프 프레임 선택 (uint8 원본 참조, 복사 없음)
        loop_idx = frame_idx % num_loop_frames
        out = loop_np[loop_idx].copy()  # uint8 복사 (~25MB, float 대비 1/4)

        # 텍스트 오버레이 (바운딩 박스 영역만 float 변환)
        for t_start, t_end, t_rgb, t_alpha, y1, y2, x1, x2 in text_cache:
            if current_time < t_start or current_time > t_end:
                continue
            t_start_d = t_start + TEXT_DELAY
            t_end_d = t_end - TEXT_DELAY
            if current_time < t_start_d or current_time > t_end_d:
                continue
            fade_in_end = t_start_d + TEXT_FADE_IN
            fade_out_start = t_end_d - TEXT_FADE_OUT - FADE_OUT
            if current_time < fade_in_end:
                a = (current_time - t_start_d) / TEXT_FADE_IN
            elif current_time > fade_out_start:
                a = (t_end_d - current_time) / (TEXT_FADE_OUT + FADE_OUT)
            else:
                a = 1.0
            a = max(0.0, min(1.0, a))

            if a > 0:
                region = out[y1:y2+1, x1:x2+1].astype(np.float32) / 255.0
                l_a = t_alpha * a
                h_r, w_r = region.shape[:2]
                grain_key = (h_r, w_r)
                if grain_key not in cached_grain or frame_idx % GRAIN_INTERVAL == 0:
                    cached_grain[grain_key] = make_grain(h_r, w_r)
                text_with_grain = (t_rgb + cached_grain[grain_key] * GRAIN_STRENGTH).clip(0, 1)
                blended = region * (1 - l_a) + text_with_grain * l_a
                out[y1:y2+1, x1:x2+1] = (blended * 255).clip(0, 255).astype(np.uint8)

        # 비주얼라이저 바 합성 (전체 영역 일괄 처리)
        vis_fade = 0.0
        for t_start, t_end, *_ in text_cache:
            if current_time < t_start or current_time > t_end:
                continue
            fade_in_end = t_start + TEXT_FADE_IN
            fade_out_start = t_end - TEXT_FADE_OUT - FADE_OUT
            if current_time < fade_in_end:
                vis_fade = (current_time - t_start) / TEXT_FADE_IN
            elif current_time > fade_out_start:
                vis_fade = (t_end - current_time) / (TEXT_FADE_OUT + FADE_OUT)
            else:
                vis_fade = 1.0
            vis_fade = max(0.0, min(1.0, vis_fade))
            break

        if vis_fade > 0:
            bar_h = all_bar_heights[frame_idx]
            bar_alpha = VIS_BAR_ALPHA * vis_fade
            half_alpha = bar_alpha * 0.5
            # 최대 바 높이로 영역 한 번에 추출
            max_bh = int(VIS_BAR_MIN_HEIGHT + np.max(bar_h) * (VIS_BAR_MAX_HEIGHT - VIS_BAR_MIN_HEIGHT))
            vis_y_top = max(0, vis_bar_y_bottom - max_bh)
            vis_region = out[vis_y_top:vis_bar_y_bottom, vis_x_start:vis_x_end].astype(np.float32) / 255.0
            # 전체 비주얼라이저 영역 그레인
            vrh, vrw = vis_region.shape[:2]
            grain_key = ('vis', vrh, vrw)
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
                # 코너 원본 저장 (상단 2px)
                corners = {}
                if local_y_bot - local_y_top >= 2 and VIS_BAR_WIDTH >= 2:
                    for dy in range(2):
                        for cx in [local_x, local_x+VIS_BAR_WIDTH-1]:
                            cy = local_y_top + dy
                            if 0 <= cy < vrh and 0 <= cx < vrw:
                                corners[(cy, cx)] = vis_region[cy, cx].copy()
                # 바 그레인 슬라이스
                bar_grain = vis_grain[local_y_top:local_y_bot, local_x:local_x+VIS_BAR_WIDTH]
                bar_with_grain = (bar_color_f + bar_grain * GRAIN_STRENGTH).clip(0, 1)
                region = vis_region[local_y_top:local_y_bot, local_x:local_x+VIS_BAR_WIDTH]
                vis_region[local_y_top:local_y_bot, local_x:local_x+VIS_BAR_WIDTH] = region * (1 - bar_alpha) + bar_with_grain * bar_alpha
                # 코너 스무딩
                for (cy, cx), orig in corners.items():
                    vis_region[cy, cx] = orig * (1 - half_alpha) + bar_color_f * half_alpha
            out[vis_y_top:vis_bar_y_bottom, vis_x_start:vis_x_end] = (vis_region * 255).clip(0, 255).astype(np.uint8)

        # 부제 합성 (첫 곡 페이드 인 후 계속 표시)
        first_start = text_cache[0][0]
        fade_in_end = first_start + TEXT_FADE_IN
        if current_time >= fade_in_end:
            sub_fade = 1.0
        elif current_time >= first_start:
            sub_fade = (current_time - first_start) / TEXT_FADE_IN
        else:
            sub_fade = 0.0
        if sub_fade > 0:
            s_region = out[sy1:sy2+1, sx1:sx2+1].astype(np.float32) / 255.0
            s_la = sub_a * sub_fade
            sh_r, sw_r = s_region.shape[:2]
            grain_key = ('sub', sh_r, sw_r)
            if grain_key not in cached_grain or frame_idx % GRAIN_INTERVAL == 0:
                cached_grain[grain_key] = make_grain(sh_r, sw_r)
            sub_with_grain = (sub_rgb + cached_grain[grain_key] * GRAIN_STRENGTH).clip(0, 1)
            blended = s_region * (1 - s_la) + sub_with_grain * s_la
            out[sy1:sy2+1, sx1:sx2+1] = (blended * 255).clip(0, 255).astype(np.uint8)

        # 전송 (이미 uint8)
        ffmpeg_proc.stdin.write(out.tobytes())

        # 진행도 표시 (30초마다)
        if (frame_idx + 1) % (FPS * 30) == 0 or frame_idx == total_frames - 1:
            pct = (frame_idx + 1) / total_frames * 100
            elapsed_min = int((frame_idx / FPS) // 60)
            elapsed_sec = int((frame_idx / FPS) % 60)
            print(f"  📊 {pct:.1f}% ({elapsed_min}:{elapsed_sec:02d}/{total_m}:{total_s:02d})")
            sys.stdout.flush()

    ffmpeg_proc.stdin.close()
    _, stderr = ffmpeg_proc.communicate()

    if ffmpeg_proc.returncode != 0 or not os.path.exists(OUTPUT_FILE):
        print("❌ 영상 생성 실패:")
        print(stderr.decode(errors='replace')[-2000:])
        return

    # 정리
    shutil.rmtree(temp_dir)

    size = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)
    print("")
    print("========================================")
    print("✅ 영상 생성 완료!")
    print(f"📁 출력: {OUTPUT_FILE}")
    print(f"📦 파일 크기: {size:.0f}MB")
    print(f"⏱  총 재생시간: {total_m}:{total_s:02d}")
    print("========================================")


if __name__ == '__main__':
    main()
