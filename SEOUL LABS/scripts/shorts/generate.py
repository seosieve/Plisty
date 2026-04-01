#!/usr/bin/env python3
"""
SEOUL LABS Shorts Generator
배경 루프 영상 + 음악 구간 + 가사 오버레이 → 쇼츠 영상
사용법: python generate.py <EP 폴더> <곡이름> <시작초>
예: python generate.py ../EP01_260323 "Forgotten Scarf at Dusk" 107
"""

import os
import sys
import json
import subprocess
import tempfile
import shutil
import wave

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PARENT_SCRIPT_DIR = os.path.dirname(SCRIPT_DIR)

sys.path.insert(0, PARENT_SCRIPT_DIR)
from lyrics import parse_lyrics_json
from generate import extract_logo_color

# ============================================================
# 인자 파싱 (config.json 지원)
# ============================================================
if len(sys.argv) < 3:
    print("사용법: python generate.py <EP 폴더> <곡이름> [시작초] [끝초]")
    print('예: python generate.py ../EP01_260323 "Forgotten Scarf at Dusk" 82 142')
    print("     시작초/끝초 생략 시 config.json에서 읽음")
    sys.exit(1)

EP_DIR = os.path.abspath(sys.argv[1])
SONG_NAME = sys.argv[2]

SONGS_DIR = os.path.join(EP_DIR, "songs")
LYRICS_DIR = os.path.join(EP_DIR, "lyrics")
IMAGES_DIR = os.path.join(EP_DIR, "images")
SHORTS_DIR = os.path.join(EP_DIR, "shorts", SONG_NAME)
CONFIG_FILE = os.path.join(SHORTS_DIR, "config.json")

BG_VIDEO = os.path.join(SHORTS_DIR, "background.mp4")
OUTPUT_FILE = os.path.join(SHORTS_DIR, "output.mp4")

# 인자 → config.json → 에러 순으로 시작점 결정
if len(sys.argv) >= 4:
    START_SEC = float(sys.argv[3])
    END_SEC = float(sys.argv[4]) if len(sys.argv) >= 5 else None
elif os.path.exists(CONFIG_FILE):
    with open(CONFIG_FILE, 'r') as f:
        _cfg = json.load(f)
    START_SEC = _cfg['start_sec']
    END_SEC = _cfg.get('end_sec')
    print(f"📋 config.json에서 로드: start={START_SEC}s, end={END_SEC}")
else:
    print(f"❌ 시작초를 지정하거나 config.json이 필요합니다: {CONFIG_FILE}")
    sys.exit(1)

AUDIO_EXTENSIONS = {'.wav', '.mp3', '.flac', '.m4a', '.aac'}

# ============================================================
# 영상 설정
# ============================================================
WIDTH = 1080
HEIGHT = 1920
FPS = 30

# Veo 워터마크 delogo 설정
DELOGO = "delogo=x=620:y=1225:w=98:h=53"

# 폰트 설정
LYRICS_FONT_PATH = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
LYRICS_FONT_INDEX = 14  # ExtraBold
LYRICS_FONT_SIZE = 76
LYRICS_ACTIVE_ALPHA = 0.25      # 현재 가사
LYRICS_INACTIVE_ALPHA = 0.25    # 이전/다음 가사
LYRICS_SCROLL_DURATION = 1.2    # 스크롤 애니메이션 시간(초)

# 레이아웃
MARGIN_LEFT = 60
MARGIN_BOTTOM = 560
LINE_HEIGHT = 90                # 2줄 가사 내 행간
ENTRY_GAP = 60                  # 가사 엔트리 간 간격
ANCHOR_Y = HEIGHT - MARGIN_BOTTOM  # 현재 가사 기준 Y

# ============================================================
# 유틸리티
# ============================================================
def get_song_file(songs_dir, song_name):
    """songs/ 폴더에서 곡이름으로 파일 찾기"""
    for f in os.listdir(songs_dir):
        ext = os.path.splitext(f)[1].lower()
        if ext not in AUDIO_EXTENSIONS:
            continue
        # 트랙 번호 제거 후 비교
        import re
        name = re.sub(r'^\d+_', '', os.path.splitext(f)[0])
        if name == song_name:
            return os.path.join(songs_dir, f)
    return None


def get_duration(filepath):
    """ffprobe로 오디오/비디오 길이 반환"""
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', filepath],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


# ============================================================
# 메인
# ============================================================
def main():
    print("🎬 SEOUL LABS Shorts Generator")
    print("========================================")

    # 테마 색상 추출 (EP images/ 폴더에서)
    bg_files = sorted(f for f in os.listdir(IMAGES_DIR)
                      if f.lower().endswith(('.png', '.jpg', '.jpeg'))) if os.path.isdir(IMAGES_DIR) else []
    if bg_files:
        theme_rgb = extract_logo_color(os.path.join(IMAGES_DIR, bg_files[0]))
        hex_color = f"#{theme_rgb[0]:02X}{theme_rgb[1]:02X}{theme_rgb[2]:02X}"
        print(f"🎨 테마 색상: {hex_color}")
    else:
        theme_rgb = (222, 242, 244)  # fallback #DEF2F4
        print("🎨 테마 색상: fallback #DEF2F4")

    # 파일 확인
    if not os.path.exists(BG_VIDEO):
        print(f"❌ 배경 영상 없음: {BG_VIDEO}")
        return

    song_file = get_song_file(SONGS_DIR, SONG_NAME)
    if not song_file:
        print(f"❌ 곡 파일 없음: {SONG_NAME}")
        return

    song_duration = get_duration(song_file)
    end_sec = END_SEC if END_SEC else song_duration
    end_sec = min(end_sec, song_duration)
    clip_duration = end_sec - START_SEC
    if clip_duration <= 0:
        print(f"❌ 시작점({START_SEC}s)이 끝점({end_sec:.1f}s)을 초과")
        return

    bg_duration = get_duration(BG_VIDEO)
    total_frames = int(clip_duration * FPS)

    print(f"🎵 곡: {SONG_NAME}")
    print(f"⏱  구간: {int(START_SEC//60)}:{int(START_SEC%60):02d} ~ {int(end_sec//60)}:{int(end_sec%60):02d} ({clip_duration:.1f}초)")
    print(f"🖼  배경: {bg_duration:.1f}초 루프")
    print(f"🎞  총 프레임: {total_frames}")

    # 가사 로드 (구간 내만 필터)
    lyrics_path = os.path.join(LYRICS_DIR, f"{SONG_NAME}.json")
    lyrics_timed = []
    if os.path.exists(lyrics_path):
        lyrics = parse_lyrics_json(lyrics_path, extend=False)
        for start_s, end_s, text in lyrics:
            # 구간 내 가사만 필터, 시간을 0 기준으로 오프셋
            if end_s < START_SEC:
                continue
            if start_s > end_sec:
                break
            adj_start = max(0, start_s - START_SEC)
            adj_end = end_s - START_SEC
            lyrics_timed.append((adj_start, adj_end, text))
        print(f"🎤 가사: {len(lyrics_timed)}줄 로드")
    else:
        print("⚠️  가사 파일 없음, 가사 없이 생성")

    # 배경 영상 프레임 추출 (delogo 적용 + 1080x1920 리사이즈)
    print("\n🖼  배경 프레임 추출 중...")
    temp_dir = tempfile.mkdtemp(prefix='shorts_render_')
    bg_frames_dir = os.path.join(temp_dir, 'bg_frames')
    os.makedirs(bg_frames_dir)

    subprocess.run([
        'ffmpeg', '-y', '-i', BG_VIDEO,
        '-vf', f'{DELOGO},scale={WIDTH}:{HEIGHT}',
        '-pix_fmt', 'rgb24',
        os.path.join(bg_frames_dir, 'frame_%05d.raw')
    ], capture_output=True)

    # raw 프레임 로드
    bg_frame_files = sorted(os.listdir(bg_frames_dir))
    bg_frame_count = len(bg_frame_files)
    print(f"  ✅ {bg_frame_count}프레임 추출 완료")

    # 배경 프레임을 메모리에 로드
    bg_frames = []
    frame_size = WIDTH * HEIGHT * 3
    for fname in bg_frame_files:
        with open(os.path.join(bg_frames_dir, fname), 'rb') as f:
            data = f.read()
            if len(data) == frame_size:
                bg_frames.append(np.frombuffer(data, dtype=np.uint8).reshape(HEIGHT, WIDTH, 3))
    bg_frame_count = len(bg_frames)

    if bg_frame_count == 0:
        print("❌ 배경 프레임 추출 실패")
        shutil.rmtree(temp_dir)
        return

    # 음원 구간 추출
    print("\n🎵 음원 구간 추출 중...")
    audio_clip = os.path.join(temp_dir, 'audio_clip.wav')
    AUDIO_FADE_IN = 1.0
    AUDIO_FADE_OUT = 2.0
    subprocess.run([
        'ffmpeg', '-y', '-ss', str(START_SEC), '-t', str(clip_duration), '-i', song_file,
        '-ar', '48000', '-ac', '2', '-c:a', 'pcm_s16le',
        '-af', f'afade=t=in:st=0:d={AUDIO_FADE_IN},afade=t=out:st={clip_duration - AUDIO_FADE_OUT}:d={AUDIO_FADE_OUT}',
        audio_clip
    ], capture_output=True)
    print("  ✅ 추출 완료")

    # 폰트 로드
    lyrics_font = ImageFont.truetype(LYRICS_FONT_PATH, LYRICS_FONT_SIZE, index=LYRICS_FONT_INDEX)

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
        OUTPUT_FILE
    ]

    ffmpeg_proc = subprocess.Popen(
        ffmpeg_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE
    )

    # 그래디언트 레이어 (화면 중앙 → 하단 100px 위, #131313 0%→60%)
    grad_arr = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
    grad_start_y = HEIGHT // 2
    grad_end_y = HEIGHT - 100
    grad_range = grad_end_y - grad_start_y
    for y in range(grad_start_y, HEIGHT):
        alpha = int(min((y - grad_start_y) / grad_range, 1.0) * 153)
        grad_arr[y, :] = [0x13, 0x13, 0x13, alpha]
    gradient_layer = Image.fromarray(grad_arr, 'RGBA')
    print("  ✅ 그래디언트 레이어 생성 완료")

    # 가사 줄바꿈 사전 계산 (렌더링 루프 밖에서 1회)
    max_w = int(WIDTH * 0.7)

    def wrap_lyric(text):
        tmp = Image.new('RGBA', (1, 1))
        d = ImageDraw.Draw(tmp)
        bbox = d.textbbox((0, 0), text, font=lyrics_font)
        text_w = bbox[2] - bbox[0]
        if text_w > max_w:
            words = text.split(' ')
            half_w = text_w / 2
            best_split = len(words) // 2
            best_diff = float('inf')
            for s in range(1, len(words)):
                l1 = ' '.join(words[:s])
                w1 = d.textbbox((0, 0), l1, font=lyrics_font)[2]
                diff = abs(w1 - half_w)
                if diff < best_diff:
                    best_diff = diff
                    best_split = s
            return [' '.join(words[:best_split]), ' '.join(words[best_split:])]
        return [text]

    lyrics_wrapped = [wrap_lyric(lt[2]) for lt in lyrics_timed]

    # 렌더링 루프
    for frame_idx in range(total_frames):
        current_time = frame_idx / FPS

        # 배경 루프
        bg_idx = frame_idx % bg_frame_count
        frame = Image.fromarray(bg_frames[bg_idx]).convert('RGBA')

        # 그래디언트 합성
        frame = Image.alpha_composite(frame, gradient_layer)

        # 스크롤 가사 (현재=선명, 나머지=블러)
        BLUR_MAX_RADIUS = 6
        blur_layer = Image.new('RGBA', (WIDTH, HEIGHT), (*theme_rgb, 0))
        blur_draw = ImageDraw.Draw(blur_layer)
        sharp_layer = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
        sharp_draw = ImageDraw.Draw(sharp_layer)

        # 현재 활성 가사 인덱스 찾기
        active_idx = -1
        for idx, (l_start, l_end, _) in enumerate(lyrics_timed):
            if l_start <= current_time < l_end:
                active_idx = idx
                break
            elif l_start > current_time:
                active_idx = idx - 1
                break
        else:
            if lyrics_timed and current_time >= lyrics_timed[-1][1]:
                active_idx = len(lyrics_timed) - 1

        def entry_height(idx):
            return len(lyrics_wrapped[idx]) * LINE_HEIGHT + ENTRY_GAP

        scroll_offset = 0.0
        scrolling = False
        scroll_target_idx = -1  # 스크롤 대상 (다음 가사)
        SCROLL_LEAD = 0.2  # 모션이 가사보다 0.2초 먼저 시작

        # 현재 active에서 다음 가사로의 스크롤 (active_idx 바뀐 후)
        if active_idx >= 1:
            l_start_cur = lyrics_timed[active_idx][0]
            scroll_start = l_start_cur - SCROLL_LEAD
            if current_time >= scroll_start and current_time < scroll_start + LYRICS_SCROLL_DURATION:
                scrolling = True
                scroll_target_idx = active_idx
                raw_progress = min(1.0, (current_time - scroll_start) / LYRICS_SCROLL_DURATION)
                progress = raw_progress
                progress = progress ** 0.8
                c1 = 1.0
                c3 = c1 + 1
                progress = 1 + c3 * (progress - 1)**3 + c1 * (progress - 1)**2
                scroll_offset = entry_height(active_idx - 1) * (progress - 1)

        # 아직 active_idx 안 바뀌었지만, 다음 가사 0.2초 전이면 미리 스크롤 시작
        if not scrolling and active_idx >= 0 and active_idx < len(lyrics_timed) - 1:
            next_start = lyrics_timed[active_idx + 1][0]
            scroll_start = next_start - SCROLL_LEAD
            if current_time >= scroll_start:
                scrolling = True
                scroll_target_idx = active_idx + 1
                raw_progress = min(1.0, (current_time - scroll_start) / LYRICS_SCROLL_DURATION)
                progress = raw_progress
                progress = progress ** 0.8
                c1 = 1.0
                c3 = c1 + 1
                progress = 1 + c3 * (progress - 1)**3 + c1 * (progress - 1)**2
                scroll_offset = entry_height(active_idx) * progress

        # 스크롤 선행 중이면 다음 가사를 활성으로 전환
        visual_active_idx = (active_idx + 1) if (scrolling and scroll_target_idx == active_idx + 1) else active_idx

        for offset in range(-5, 6):
            line_idx = active_idx + offset
            if line_idx < 0 or line_idx >= len(lyrics_timed):
                continue

            l_start, l_end, lyric_text = lyrics_timed[line_idx]
            lines = lyrics_wrapped[line_idx]
            is_active = (line_idx == visual_active_idx)

            distance = abs(line_idx - visual_active_idx)
            if is_active:
                alpha = LYRICS_ACTIVE_ALPHA
            else:
                # 거리별 투명도
                fade_map = {1: 0.25, 2: 0.1}
                alpha = fade_map.get(distance, 0.0)
            fill_color = (*theme_rgb, int(alpha * 255))

            # Y 위치 계산
            if offset == 0:
                base_y = ANCHOR_Y
            elif offset > 0:
                base_y = ANCHOR_Y
                for k in range(active_idx, active_idx + offset):
                    if 0 <= k < len(lyrics_timed):
                        base_y += entry_height(k)
            else:
                base_y = ANCHOR_Y
                for k in range(active_idx - 1, active_idx + offset - 1, -1):
                    if 0 <= k < len(lyrics_timed):
                        base_y -= entry_height(k)

            base_y -= scroll_offset

            if base_y + len(lines) * LINE_HEIGHT < 0 or base_y > HEIGHT:
                continue

            draw = sharp_draw if is_active else blur_draw
            for i, line in enumerate(lines):
                ly = base_y + i * LINE_HEIGHT
                if 0 <= ly <= HEIGHT:
                    draw.text((MARGIN_LEFT, ly), line, font=lyrics_font, fill=fill_color)

            # 카라오케 채우기 (활성 가사만)
            if is_active and l_start <= current_time:
                # 모션 시작(0.2초 선행) 전에 채우기 완료되도록
                if line_idx < len(lyrics_timed) - 1:
                    next_s = lyrics_timed[line_idx + 1][0]
                    fill_end = min(l_end, next_s - SCROLL_LEAD)
                else:
                    fill_end = l_end
                progress = min(1.0, (current_time - l_start) / max(0.01, fill_end - l_start))
                fill_alpha = int(0.9 * 255)
                fill_color_top = (*theme_rgb, fill_alpha)
                # 전체 텍스트 폭 계산 (줄별)
                line_widths = []
                for line in lines:
                    bb = sharp_draw.textbbox((0, 0), line, font=lyrics_font)
                    line_widths.append(bb[2] - bb[0])
                FADE_PX = 80
                # 그라데이션이 텍스트를 지나가도록 여유 포함
                total_w = sum(lw + FADE_PX for lw in line_widths)
                filled_w = progress * total_w
                for i, line in enumerate(lines):
                    ly = base_y + i * LINE_HEIGHT
                    if ly < 0 or ly > HEIGHT:
                        continue
                    if filled_w <= 0:
                        break
                    lw = line_widths[i]
                    line_filled = min(filled_w, lw + FADE_PX)
                    filled_w -= (lw + FADE_PX)
                    # 채워진 부분만 렌더 (경계 그라데이션)
                    fill_w = int(line_filled)
                    fade_start = max(0, fill_w - FADE_PX)
                    render_w = int(lw) + 2
                    txt_tmp = Image.new('RGBA', (render_w, LINE_HEIGHT), (0, 0, 0, 0))
                    tmp_d = ImageDraw.Draw(txt_tmp)
                    tmp_d.text((0, 0), line, font=lyrics_font, fill=fill_color_top)
                    if fill_w < int(lw) + FADE_PX:
                        px = txt_tmp.load()
                        for fx in range(min(fade_start, render_w), min(fill_w, render_w)):
                            fade = 1.0 - (fx - fade_start) / FADE_PX
                            for fy in range(txt_tmp.height):
                                r, g, b, a = px[fx, fy]
                                px[fx, fy] = (r, g, b, int(a * fade))
                        for fx in range(min(fill_w, render_w), render_w):
                            for fy in range(txt_tmp.height):
                                px[fx, fy] = (0, 0, 0, 0)
                    sharp_layer.paste(txt_tmp, (MARGIN_LEFT, int(ly)), txt_tmp)

        blur_layer = blur_layer.filter(ImageFilter.GaussianBlur(radius=BLUR_MAX_RADIUS))
        frame = Image.alpha_composite(frame, blur_layer)
        frame = Image.alpha_composite(frame, sharp_layer)

        # RGB로 변환하여 전송
        frame_rgb = frame.convert('RGB')
        ffmpeg_proc.stdin.write(np.array(frame_rgb).tobytes())

        # 진행도 (10초마다)
        if (frame_idx + 1) % (FPS * 10) == 0 or frame_idx == total_frames - 1:
            pct = (frame_idx + 1) / total_frames * 100
            print(f"  📊 {pct:.1f}%")
            sys.stdout.flush()

    ffmpeg_proc.stdin.close()
    _, stderr = ffmpeg_proc.communicate()

    if ffmpeg_proc.returncode != 0 or not os.path.exists(OUTPUT_FILE):
        print("❌ 영상 생성 실패")
        print(stderr.decode(errors='replace')[-2000:])
        shutil.rmtree(temp_dir)
        return

    # 정리
    shutil.rmtree(temp_dir)

    # config.json 저장
    config = {"start_sec": START_SEC, "end_sec": end_sec}
    with open(CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

    size = os.path.getsize(OUTPUT_FILE) / (1024 * 1024)
    print("")
    print("========================================")
    print("✅ 쇼츠 생성 완료!")
    print(f"📁 출력: {OUTPUT_FILE}")
    print(f"📦 파일 크기: {size:.1f}MB")
    print(f"⏱  길이: {clip_duration:.1f}초")
    print("========================================")


if __name__ == '__main__':
    main()
