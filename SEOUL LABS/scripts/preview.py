#!/usr/bin/env python3
"""레이아웃 미리보기 — 배경 + 파티클 + visualizer를 PIL로, 텍스트는 ffmpeg drawtext로 오버레이"""

import os
import sys
import subprocess
import random
import tempfile
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# EP 폴더
if len(sys.argv) < 2:
    print("사용법: python preview.py <EP 폴더 경로>")
    sys.exit(1)

EP_DIR = os.path.abspath(sys.argv[1])
SONGS_DIR = os.path.join(EP_DIR, "songs")
IMAGES_DIR = os.path.join(EP_DIR, "images")
LYRICS_DIR = os.path.join(EP_DIR, "lyrics")
OUTPUT_DIR = os.path.join(EP_DIR, "output")

# generate.py와 동일한 설정 import
sys.path.insert(0, SCRIPT_DIR)
from generate import (
    WIDTH, HEIGHT, SCALE,
    BOTTOM_MARGIN, LYRICS_TO_TITLE, TITLE_TO_VIS,
    NUM_BARS, BAR_WIDTH, BAR_GAP, BAR_MAX_HEIGHT, BAR_MIN_HEIGHT,
    BAR_ALPHA, BAR_Y_CENTER,
    FONT_PATH, TEXT_FONT_SIZE, TEXT_COLOR, TEXT_ALPHA, TEXT_X, TEXT_Y,
    LYRICS_FONT_PATH, LYRICS_FONT_SIZE, LYRICS_COLOR, LYRICS_ALPHA, LYRICS_X, LYRICS_Y,
    NUM_PARTICLES, MIN_SIZE, MAX_SIZE, MIN_ALPHA, MAX_ALPHA,
    PARTICLE_COLOR, BLUR_RADIUS,
    AUDIO_EXTENSIONS,
    load_image_aspect_fill,
    render_bars, get_audio_data, precompute_bar_heights, FPS,
)
from lyrics import parse_lyrics_json


def main():
    # 배경
    bg_files = [f for f in os.listdir(IMAGES_DIR)
                if f.lower().endswith(('.png', '.jpg', '.jpeg'))]
    if not bg_files:
        print("❌ images/ 폴더에 배경 이미지가 없습니다")
        sys.exit(1)

    bg_path = os.path.join(IMAGES_DIR, bg_files[0])
    print(f"🖼  배경: {bg_files[0]}")
    canvas = load_image_aspect_fill(bg_path)

    # 파티클
    particle_layer = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
    random.seed(42)
    for _ in range(NUM_PARTICLES):
        x = random.uniform(0, WIDTH)
        y = random.uniform(0, HEIGHT)
        size = random.uniform(MIN_SIZE, MAX_SIZE)
        alpha = random.randint(MIN_ALPHA, MAX_ALPHA)
        blur_r = BLUR_RADIUS * (size / MIN_SIZE) * 0.4
        margin = int(size + blur_r * 3) + 2
        patch_size = margin * 2 + 1
        ps = Image.new('RGBA', (patch_size, patch_size), (0, 0, 0, 0))
        pd = ImageDraw.Draw(ps)
        si = int(round(size))
        pd.ellipse(
            [margin - si, margin - si, margin + si, margin + si],
            fill=(*PARTICLE_COLOR, alpha)
        )
        ps = ps.filter(ImageFilter.GaussianBlur(radius=blur_r))
        px, py = int(x) - margin, int(y) - margin
        particle_layer.alpha_composite(ps, (max(0, px), max(0, py)))
    canvas.alpha_composite(particle_layer)

    # 곡 정보 가져오기
    song_files = sorted([
        f for f in os.listdir(SONGS_DIR)
        if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
    ]) if os.path.isdir(SONGS_DIR) else []
    import re
    raw_name = os.path.splitext(song_files[0])[0] if song_files else "Sample Track"
    song_name = re.sub(r'^\d+_', '', raw_name)

    # Visualizer 바 (generate와 동일한 파이프라인)
    STATIC_BARS = 3
    total_bars = STATIC_BARS + NUM_BARS + STATIC_BARS
    total_bar_width = total_bars * BAR_WIDTH + (total_bars - 1) * BAR_GAP
    start_x = 80 * SCALE
    bar_positions = [start_x + i * (BAR_WIDTH + BAR_GAP) for i in range(total_bars)]

    bar_heights = np.zeros(NUM_BARS)
    if song_files:
        audio_path = os.path.join(SONGS_DIR, song_files[0])
        tmp_audio = tempfile.mkdtemp(prefix='preview_audio_')
        try:
            samples, sr = get_audio_data(audio_path, tmp_audio)
            total_frames = int(len(samples) / sr * FPS)
            all_bar_heights = precompute_bar_heights(samples, sr, total_frames)
            mid_frame = total_frames // 2
            bar_heights = all_bar_heights[mid_frame]
        finally:
            shutil.rmtree(tmp_audio)

    bar_layer = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(bar_layer)
    render_bars(draw, bar_heights, bar_positions, total_bars, STATIC_BARS)
    canvas.alpha_composite(bar_layer)

    # 제목/가사 텍스트 준비
    title_text = f"01. {song_name}".upper()

    lyrics_text = ""
    lyrics_path = os.path.join(LYRICS_DIR, f"{song_name}.json")
    if os.path.exists(lyrics_path):
        lines = parse_lyrics_json(lyrics_path)
        if lines:
            has_ko = any(any('\uac00' <= ch <= '\ud7a3' for ch in l[2]) for l in lines)
            if has_ko:
                mixed = [l for l in lines
                         if any('\uac00' <= ch <= '\ud7a3' for ch in l[2])
                         and any('a' <= ch.lower() <= 'z' for ch in l[2])]
                if mixed:
                    lyrics_text = max(mixed, key=lambda l: len(l[2]))[2]
                else:
                    lyrics_text = max(lines, key=lambda l: len(l[2]))[2]
            else:
                lyrics_text = max(lines, key=lambda l: len(l[2]))[2]
    if not lyrics_text:
        lyrics_text = "Sample lyrics here"

    # PIL 프레임을 임시 PNG로 저장
    tmp_dir = tempfile.mkdtemp(prefix='preview_')
    base_png = os.path.join(tmp_dir, 'base.png')
    canvas.convert('RGB').save(base_png)

    # 제목/가사 텍스트 파일 (ffmpeg textfile용)
    title_file = os.path.join(tmp_dir, 'title.txt')
    with open(title_file, 'w', encoding='utf-8') as f:
        f.write(title_text)

    lyrics_file = os.path.join(tmp_dir, 'lyrics.txt')
    with open(lyrics_file, 'w', encoding='utf-8') as f:
        f.write(lyrics_text)

    # ffmpeg drawtext — generate.py와 동일한 필터
    vf = (
        f"drawtext=fontfile='{FONT_PATH}'"
        f":textfile='{title_file}'"
        f":fontsize={TEXT_FONT_SIZE}:fontcolor={TEXT_COLOR}"
        f":x={TEXT_X}:y={TEXT_Y}"
        f":alpha={TEXT_ALPHA}"
        f","
        f"drawtext=fontfile='{LYRICS_FONT_PATH}'"
        f":textfile='{lyrics_file}'"
        f":fontsize={LYRICS_FONT_SIZE}:fontcolor={LYRICS_COLOR}"
        f":x={LYRICS_X}:y={LYRICS_Y}"
        f":alpha={LYRICS_ALPHA}"
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ep_name = os.path.basename(EP_DIR)
    out_path = os.path.join(OUTPUT_DIR, f"{ep_name}_preview.png")

    subprocess.run([
        'ffmpeg', '-y',
        '-i', base_png,
        '-vf', vf,
        '-frames:v', '1',
        out_path
    ], capture_output=True)

    shutil.rmtree(tmp_dir)

    print(f"✅ 미리보기 저장: {out_path}")
    subprocess.run(["open", out_path])


if __name__ == "__main__":
    main()
