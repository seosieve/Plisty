#!/usr/bin/env python3
"""
raw. Shorts Generator
배경 루프 영상 + 음악 구간 → 쇼츠 영상
사용법: python3 generate.py <EP 폴더> <곡이름> <시작초> [끝초]
예: python3 generate.py ../EP01_260402 "치고달려라 2010" 30 90
"""

import os
import re
import sys
import subprocess

# ============================================================
# 설정
# ============================================================
WIDTH = 1080
HEIGHT = 1920
FPS = 30
MAX_DURATION = 60.0
AUDIO_FADE_IN = 1.0
AUDIO_FADE_OUT = 2.0
AUDIO_EXTENSIONS = {'.wav', '.mp3', '.flac', '.m4a', '.aac'}

# Veo 워터마크 패치 (background.png에서 해당 영역을 잘라 덮어씌움)
VEO_X, VEO_Y, VEO_W, VEO_H = 960, 1845, 120, 75


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

    print(f"🎵 곡: {song_name}")
    print(f"⏱  구간: {int(start_sec//60)}:{int(start_sec%60):02d} ~ {int((start_sec+clip_duration)//60)}:{int((start_sec+clip_duration)%60):02d} ({clip_duration:.1f}초)")
    print(f"🖼  배경: {get_duration(bg_video):.1f}초 루프")

    # ffmpeg 입력 및 필터 구성
    ffmpeg_inputs = ['-stream_loop', '-1', '-i', bg_video]
    vf = f'scale={WIDTH}:{HEIGHT}'

    if os.path.exists(bg_image):
        ffmpeg_inputs += ['-i', bg_image]
        filter_complex = (
            f'[0:v]{vf}[main];'
            f'[1:v]crop={VEO_W}:{VEO_H}:{VEO_X}:{VEO_Y}[patch];'
            f'[main][patch]overlay={VEO_X}:{VEO_Y}[vout]'
        )
        audio_idx = 2
    else:
        print("⚠️  background.png 없음, 워터마크 패치 건너뜀")
        filter_complex = f'[0:v]{vf}[vout]'
        audio_idx = 1

    print("\n🎬 영상 생성 중...")
    subprocess.run([
        'ffmpeg', '-y',
        *ffmpeg_inputs,
        '-ss', str(start_sec), '-t', str(clip_duration), '-i', song_file,
        '-filter_complex', filter_complex,
        '-map', '[vout]', '-map', f'{audio_idx}:a:0',
        '-af', f'afade=t=in:st=0:d={AUDIO_FADE_IN},afade=t=out:st={clip_duration - AUDIO_FADE_OUT}:d={AUDIO_FADE_OUT}',
        '-c:v', 'libx264', '-preset', 'medium', '-crf', '18',
        '-c:a', 'aac', '-b:a', '192k',
        '-pix_fmt', 'yuv420p',
        '-t', str(clip_duration),
        '-r', str(FPS),
        output_file
    ])

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
