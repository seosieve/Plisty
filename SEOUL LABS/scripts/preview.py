#!/usr/bin/env python3
"""레이아웃 미리보기 — 배경 + 파티클 + visualizer + 텍스트를 PIL로 렌더링"""

import os
import sys
import random
import tempfile
import shutil

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# EP 폴더
if len(sys.argv) < 2:
    print("사용법: python preview.py <EP 폴더 경로>")
    sys.exit(1)

EP_DIR = os.path.abspath(sys.argv[1])
SONGS_DIR = os.path.join(EP_DIR, "songs")
IMAGES_DIR = os.path.join(EP_DIR, "images")
LYRICS_DIR = os.path.join(EP_DIR, "lyrics")
OUTPUT_DIR = os.path.join(EP_DIR, "outputs")

sys.path.insert(0, SCRIPT_DIR)
from generate import (
    WIDTH, HEIGHT, SCALE,
    MARGIN_LEFT, MARGIN_BOTTOM,
    GAP_LYRICS_TITLE, GAP_TITLE_VIS,
    NUM_BARS, BAR_WIDTH, BAR_GAP, BAR_MAX_HEIGHT, BAR_MIN_HEIGHT,
    BAR_ALPHA, BAR_Y_CENTER,
    FONT_PATH, TEXT_FONT_SIZE, TEXT_ALPHA,
    LYRICS_FONT_PATH, LYRICS_FONT_SIZE, LYRICS_ALPHA,
    NUM_PARTICLES, MIN_SIZE, MAX_SIZE, MIN_ALPHA, MAX_ALPHA,
    PARTICLE_COLOR, BLUR_RADIUS,
    AUDIO_EXTENSIONS,
    load_image_aspect_fill, extract_logo_color,
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

    # 테마 색상 자동 추출
    theme_rgb = extract_logo_color(bg_path)
    hex_color = f"#{theme_rgb[0]:02X}{theme_rgb[1]:02X}{theme_rgb[2]:02X}"
    print(f"🎨 테마 색상: {hex_color}")

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

    # 곡 정보
    song_files = sorted([
        f for f in os.listdir(SONGS_DIR)
        if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
    ]) if os.path.isdir(SONGS_DIR) else []
    import re
    raw_name = os.path.splitext(song_files[0])[0] if song_files else "Sample Track"
    song_name = re.sub(r'^\d+_', '', raw_name)

    # Visualizer 바
    STATIC_BARS = 3
    total_bars = STATIC_BARS + NUM_BARS + STATIC_BARS
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
    render_bars(draw, bar_heights, bar_positions, total_bars, STATIC_BARS, theme_rgb)
    canvas.alpha_composite(bar_layer)

    # 텍스트 (PIL 렌더링)
    PIL_Y_OFFSET = -4 * SCALE
    TEXT_Y_PX = HEIGHT - (MARGIN_BOTTOM + LYRICS_FONT_SIZE + GAP_LYRICS_TITLE + TEXT_FONT_SIZE) + PIL_Y_OFFSET - 14 * SCALE
    LYRICS_Y_PX = HEIGHT - MARGIN_BOTTOM - LYRICS_FONT_SIZE + PIL_Y_OFFSET

    title_font = ImageFont.truetype(FONT_PATH, TEXT_FONT_SIZE)
    lyrics_font = ImageFont.truetype(LYRICS_FONT_PATH, LYRICS_FONT_SIZE)

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

    text_layer = Image.new('RGBA', (WIDTH, HEIGHT), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_layer)
    text_draw.text((MARGIN_LEFT, TEXT_Y_PX), title_text,
                   font=title_font, fill=(*theme_rgb, int(TEXT_ALPHA * 255)))
    text_draw.text((MARGIN_LEFT, LYRICS_Y_PX), lyrics_text,
                   font=lyrics_font, fill=(*theme_rgb, int(LYRICS_ALPHA * 255)))
    canvas.alpha_composite(text_layer)

    # 저장
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ep_name = os.path.basename(EP_DIR)
    out_path = os.path.join(OUTPUT_DIR, f"{ep_name}_preview.png")
    canvas.convert('RGB').save(out_path)

    print(f"✅ 미리보기 저장: {out_path}")
    import subprocess
    subprocess.run(["open", out_path])


if __name__ == "__main__":
    main()
