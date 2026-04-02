#!/usr/bin/env python3
"""레이아웃 미리보기 — 루프 영상 첫 프레임 + 텍스트 오버레이"""

import os
import sys
import re
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

if len(sys.argv) < 2:
    print("사용법: python3 preview.py <EP 폴더 경로>")
    sys.exit(1)

EP_DIR = os.path.abspath(sys.argv[1])
SONGS_DIR = os.path.join(EP_DIR, "songs")
LOOPS_DIR = os.path.join(EP_DIR, "loops")
OUTPUT_DIR = os.path.join(EP_DIR, "outputs")

sys.path.insert(0, SCRIPT_DIR)
from generate import (
    NUM_FONT_PATH, NUM_FONT_SIZE, NUM_COLOR, NUM_ALPHA,
    TITLE_FONT_PATH, TITLE_FONT_SIZE, TITLE_COLOR, TITLE_ALPHA, TITLE_GAP,
    TEXT_X, TEXT_MARGIN_TOP, TITLE_MAX_WIDTH,
    SUB_FONT_PATH, SUB_FONT_SIZE, SUB_COLOR, SUB_ALPHA, SUB_MARGIN_BOTTOM,
    AUDIO_EXTENSIONS, SCALE,
)


def strip_track_prefix(name):
    return re.sub(r'^\d+_', '', name)


def main():
    # 루프 영상에서 첫 프레임 추출
    loop_file = None
    if os.path.isdir(LOOPS_DIR):
        loop_files = sorted(f for f in os.listdir(LOOPS_DIR) if f.lower().endswith(('.mp4', '.mov', '.webm')))
        if loop_files:
            loop_file = os.path.join(LOOPS_DIR, loop_files[0])

    if not loop_file:
        print("❌ loops 폴더에 영상 파일이 없습니다")
        sys.exit(1)

    # 부제: EP 번호 + 루프 파일명
    ep_name = os.path.basename(EP_DIR)
    ep_num = int(re.search(r'EP(\d+)', ep_name).group(1))
    loop_name = os.path.splitext(os.path.basename(loop_file))[0]
    SUB_TEXT = f"{ep_num} {loop_name}"
    if '-y' not in sys.argv:
        print(f"\n📝 부제: {SUB_TEXT}")
        confirm = input("   이대로 진행할까요? (y/n): ").strip().lower()
        if confirm != 'y':
            print("❌ 취소되었습니다. 루프 파일명을 부제에 맞게 변경해주세요.")
            sys.exit(1)

    print(f"🔁 루프 영상: {os.path.basename(loop_file)}")
    print(f"📝 부제: {SUB_TEXT}")

    # ffprobe로 해상도
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-select_streams', 'v:0',
         '-show_entries', 'stream=width,height', '-of', 'csv=p=0', loop_file],
        capture_output=True, text=True
    )
    parts = result.stdout.strip().split(',')
    src_w, src_h = int(parts[0]), int(parts[1])
    width, height = src_w * SCALE, src_h * SCALE

    # 첫 프레임 추출 (SCALE 적용)
    scale_filter = ['-vf', f'scale={width}:{height}:flags=lanczos'] if SCALE > 1 else []
    result = subprocess.run([
        'ffmpeg', '-i', loop_file,
        '-frames:v', '1', *scale_filter,
        '-f', 'rawvideo', '-pix_fmt', 'rgb24',
        '-v', 'quiet', '-'
    ], capture_output=True)

    canvas = Image.frombytes('RGB', (width, height), result.stdout).convert('RGBA')

    # 곡 정보
    song_files = sorted([
        f for f in os.listdir(SONGS_DIR)
        if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
    ]) if os.path.isdir(SONGS_DIR) else []

    if not song_files:
        print("❌ songs 폴더에 오디오 파일이 없습니다")
        sys.exit(1)

    # 첫 곡 기준으로 미리보기
    first_file = song_files[0]
    track_num = int(os.path.splitext(first_file)[0].split('_')[0]) if first_file[0].isdigit() else 1
    num_text = f"{track_num:02d}."
    title_raw = strip_track_prefix(os.path.splitext(first_file)[0])

    # 텍스트 오버레이
    num_font = ImageFont.truetype(NUM_FONT_PATH, NUM_FONT_SIZE)
    title_font = ImageFont.truetype(TITLE_FONT_PATH, TITLE_FONT_SIZE)

    # 줄바꿈 적용
    def wrap_title(text, font, max_width):
        bbox = font.getbbox(text)
        if bbox[2] - bbox[0] <= max_width:
            return text
        for i in range(len(text) - 1, 0, -1):
            if text[i] == ' ':
                line1 = text[:i]
                if font.getbbox(line1)[2] - font.getbbox(line1)[0] <= max_width:
                    return line1 + '\n' + wrap_title(text[i+1:], font, max_width)
        for i in range(len(text), 0, -1):
            if font.getbbox(text[:i])[2] - font.getbbox(text[:i])[0] <= max_width:
                return text[:i] + '\n' + wrap_title(text[i:], font, max_width)
        return text

    title_text = wrap_title(title_raw, title_font, TITLE_MAX_WIDTH)
    print(f"🎵 미리보기 곡: {num_text} {title_raw}")

    text_layer = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)

    # 곡 번호
    num_bbox = num_font.getbbox(num_text)
    num_tw = num_bbox[2] - num_bbox[0]
    num_th = num_bbox[3] - num_bbox[1]
    num_x = TEXT_X
    num_y = TEXT_MARGIN_TOP
    text_draw.text((num_x, num_y), num_text, font=num_font,
                   fill=(*NUM_COLOR, int(NUM_ALPHA * 255)))

    # 곡 제목
    title_x = TEXT_X
    title_y = num_y + num_th + TITLE_GAP
    text_draw.text((title_x, title_y), title_text, font=title_font,
                   fill=(*TITLE_COLOR, int(TITLE_ALPHA * 255)), spacing=20 * SCALE)

    # 인공 그레인 합성
    from generate import GRAIN_STRENGTH, GRAIN_SIZE
    b = np.array(canvas, dtype=np.float32) / 255.0
    l = np.array(text_layer, dtype=np.float32) / 255.0
    b_rgb, l_rgb, l_a = b[:, :, :3], l[:, :, :3], l[:, :, 3:4]
    h, w = b_rgb.shape[:2]
    from generate import GRAIN_COLOR
    if GRAIN_SIZE > 1:
        mono = np.random.randn(h // GRAIN_SIZE + 1, w // GRAIN_SIZE + 1, 1).astype(np.float32)
        color = np.random.randn(h // GRAIN_SIZE + 1, w // GRAIN_SIZE + 1, 3).astype(np.float32)
        small = mono * (1 - GRAIN_COLOR) + color * GRAIN_COLOR
        grain = np.repeat(np.repeat(small, GRAIN_SIZE, axis=0), GRAIN_SIZE, axis=1)[:h, :w]
    else:
        mono = np.random.randn(h, w, 1).astype(np.float32)
        color = np.random.randn(h, w, 3).astype(np.float32)
        grain = mono * (1 - GRAIN_COLOR) + color * GRAIN_COLOR
    text_with_grain = (l_rgb + grain * GRAIN_STRENGTH).clip(0, 1)
    b[:, :, :3] = b_rgb * (1 - l_a) + text_with_grain * l_a
    canvas = Image.fromarray((b * 255).clip(0, 255).astype(np.uint8), canvas.mode)

    # 비주얼라이저 바 (정적 미리보기 - 랜덤 높이, 그레인 적용)
    from generate import (VIS_NUM_BARS, VIS_BAR_WIDTH, VIS_BAR_GAP,
                          VIS_BAR_MAX_HEIGHT, VIS_BAR_MIN_HEIGHT,
                          VIS_BAR_ALPHA, VIS_BAR_COLOR, VIS_MARGIN_BOTTOM)
    canvas_np = np.array(canvas, dtype=np.float32) / 255.0
    vis_y_bottom = height - VIS_MARGIN_BOTTOM
    np.random.seed(42)
    fake_heights = np.random.uniform(0.2, 0.8, VIS_NUM_BARS)
    bar_color = np.array(VIS_BAR_COLOR, dtype=np.float32) / 255.0
    bar_alpha = VIS_BAR_ALPHA
    half_alpha = bar_alpha * 0.5
    for i in range(VIS_NUM_BARS):
        bh = int(VIS_BAR_MIN_HEIGHT + fake_heights[i] * (VIS_BAR_MAX_HEIGHT - VIS_BAR_MIN_HEIGHT))
        bx = TEXT_X + i * (VIS_BAR_WIDTH + VIS_BAR_GAP)
        by_top = vis_y_bottom - bh
        by_bot = vis_y_bottom
        if bx + VIS_BAR_WIDTH <= width:
            corners = {}
            if by_bot - by_top >= 2 and VIS_BAR_WIDTH >= 2:
                for dy in range(2):
                    for cx in [bx, bx+VIS_BAR_WIDTH-1]:
                        cy = by_top + dy
                        if 0 <= cy < height and 0 <= cx < width:
                            corners[(cy, cx)] = canvas_np[cy, cx, :3].copy()
            bh_r, bw_r = by_bot - by_top, VIS_BAR_WIDTH
            if GRAIN_SIZE > 1:
                mono = np.random.randn(bh_r // GRAIN_SIZE + 1, bw_r // GRAIN_SIZE + 1, 1).astype(np.float32)
                color = np.random.randn(bh_r // GRAIN_SIZE + 1, bw_r // GRAIN_SIZE + 1, 3).astype(np.float32)
                small = mono * (1 - GRAIN_COLOR) + color * GRAIN_COLOR
                grain = np.repeat(np.repeat(small, GRAIN_SIZE, axis=0), GRAIN_SIZE, axis=1)[:bh_r, :bw_r]
            else:
                mono = np.random.randn(bh_r, bw_r, 1).astype(np.float32)
                color = np.random.randn(bh_r, bw_r, 3).astype(np.float32)
                grain = mono * (1 - GRAIN_COLOR) + color * GRAIN_COLOR
            bar_with_grain = (bar_color + grain * GRAIN_STRENGTH).clip(0, 1)
            region = canvas_np[by_top:by_bot, bx:bx+VIS_BAR_WIDTH, :3]
            canvas_np[by_top:by_bot, bx:bx+VIS_BAR_WIDTH, :3] = region * (1 - bar_alpha) + bar_with_grain * bar_alpha
            for (cy, cx), orig in corners.items():
                canvas_np[cy, cx, :3] = orig * (1 - half_alpha) + bar_color * half_alpha

    # 부제 (그레인 적용)
    from generate import SUB_FONT_WEIGHT
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
    sub_rgb = sub_arr[sy1:sy2+1, sx1:sx2+1, :3]
    sub_a = sub_arr[sy1:sy2+1, sx1:sx2+1, 3:4]
    sh_r, sw_r = sub_a.shape[:2]
    if GRAIN_SIZE > 1:
        mono = np.random.randn(sh_r // GRAIN_SIZE + 1, sw_r // GRAIN_SIZE + 1, 1).astype(np.float32)
        color = np.random.randn(sh_r // GRAIN_SIZE + 1, sw_r // GRAIN_SIZE + 1, 3).astype(np.float32)
        small = mono * (1 - GRAIN_COLOR) + color * GRAIN_COLOR
        grain = np.repeat(np.repeat(small, GRAIN_SIZE, axis=0), GRAIN_SIZE, axis=1)[:sh_r, :sw_r]
    else:
        mono = np.random.randn(sh_r, sw_r, 1).astype(np.float32)
        color = np.random.randn(sh_r, sw_r, 3).astype(np.float32)
        grain = mono * (1 - GRAIN_COLOR) + color * GRAIN_COLOR
    sub_with_grain = (sub_rgb + grain * GRAIN_STRENGTH).clip(0, 1)
    s_region = canvas_np[sy1:sy2+1, sx1:sx2+1, :3]
    canvas_np[sy1:sy2+1, sx1:sx2+1, :3] = s_region * (1 - sub_a) + sub_with_grain * sub_a
    canvas = Image.fromarray((canvas_np * 255).clip(0, 255).astype(np.uint8), canvas.mode)

    # 저장
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ep_name = os.path.basename(EP_DIR)
    out_path = os.path.join(OUTPUT_DIR, f"{ep_name}_preview.png")
    canvas.convert('RGB').save(out_path)

    print(f"✅ 미리보기 저장: {out_path}")
    subprocess.run(["open", out_path])


if __name__ == "__main__":
    main()
