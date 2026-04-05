#!/usr/bin/env python3
"""SUNO 플레이리스트에서 wav 파일 일괄 다운로드 + 오디오 분석/시퀀싱"""

import sys
import os
import subprocess
import requests
import time

import numpy as np
import librosa

BASE = "https://studio-api.prod.suno.com"
KEYS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


def get_token_from_safari():
    """Safari에서 SUNO __session 쿠키 자동 추출 (모든 탭 탐색)"""
    script = '''
    tell application "Safari"
        repeat with w in windows
            repeat with t in tabs of w
                if URL of t starts with "https://suno.com" or URL of t starts with "https://studio-api.prod.suno.com" then
                    return do JavaScript "document.cookie" in t
                end if
            end repeat
        end repeat
    end tell
    return ""
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode != 0:
            return None
        for part in result.stdout.split(";"):
            part = part.strip()
            if part.startswith("__session=") and not part.startswith("__session_"):
                return part[len("__session="):]
    except Exception:
        pass
    return None


def get_playlist_songs(playlist_id, headers):
    """플레이리스트에서 곡 정보 가져오기"""
    r = requests.get(f"{BASE}/api/playlist/{playlist_id}/", headers=headers)
    data = r.json()
    songs = []
    for clip in data.get("playlist_clips", []):
        c = clip.get("clip", clip)
        songs.append((c["id"], c["title"]))
    return songs


def download_wav(songs, headers, download_dir):
    """전곡 wav 변환 요청 후 다운로드"""
    os.makedirs(download_dir, exist_ok=True)

    print(f"📦 {len(songs)}곡 wav 변환 요청...")
    for song_id, title in songs:
        r = requests.post(f"{BASE}/api/gen/{song_id}/convert_wav/", headers=headers)
        status = "✅" if r.status_code == 204 else f"❌ ({r.status_code})"
        print(f"  {status} {title}")

    print()
    print("⏳ wav 다운로드 중...")
    for song_id, title in songs:
        for _ in range(60):
            resp = requests.get(f"{BASE}/api/gen/{song_id}/wav_file/", headers=headers)
            if resp.status_code == 200:
                wav_url = resp.json().get("wav_file_url", "")
                if wav_url:
                    r = requests.get(wav_url)
                    filepath = os.path.join(download_dir, f"{title}.wav")
                    with open(filepath, "wb") as f:
                        f.write(r.content)
                    size_mb = len(r.content) / 1024 / 1024
                    print(f"  ✅ {title} ({size_mb:.1f}MB)")
                    break
            time.sleep(2)
        else:
            print(f"  ❌ {title} 타임아웃")


def analyze_songs(download_dir, song_titles):
    """다운로드된 곡들의 오디오 특성 분석 (플레이리스트 순서 유지)"""
    wav_files = [f"{title}.wav" for title in song_titles if os.path.exists(os.path.join(download_dir, f"{title}.wav"))]
    if not wav_files:
        return []

    print(f"📊 {len(wav_files)}곡 오디오 분석 중...")
    results = []
    for f in wav_files:
        filepath = os.path.join(download_dir, f)
        title = os.path.splitext(f)[0]
        y, sr = librosa.load(filepath, sr=None)

        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(tempo[0]) if hasattr(tempo, '__len__') else float(tempo)
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        key_idx = int(np.argmax(np.mean(chroma, axis=1)))
        rms = float(np.mean(librosa.feature.rms(y=y)))
        spectral = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
        duration = librosa.get_duration(y=y, sr=sr)

        results.append({
            'title': title,
            'bpm': bpm,
            'key': KEYS[key_idx],
            'key_idx': key_idx,
            'energy': rms,
            'brightness': spectral,
            'duration': duration,
        })
        print(f"  ✅ {title}: {bpm:.0f}BPM, {KEYS[key_idx]}, energy={rms:.4f}, {duration:.0f}s")

    return results


def suggest_sequence(results, first_idx):
    """첫 곡 기준 에너지 커브 시퀀싱 (첫곡→빌드업→피크→차분)"""
    first = results[first_idx]
    remaining = [r for i, r in enumerate(results) if i != first_idx]

    if len(remaining) <= 1:
        return [first] + remaining

    def key_distance(k1, k2):
        d = abs(k1 - k2)
        return min(d, 12 - d)

    # 첫 곡 에너지 기준으로 나머지를 빌드업→피크→차분 배치
    sorted_by_energy = sorted(remaining, key=lambda x: x['energy'])
    n = len(sorted_by_energy)

    low = sorted_by_energy[:n // 3]
    mid = sorted_by_energy[n // 3: 2 * n // 3]
    high = sorted_by_energy[2 * n // 3:]

    # 첫 곡 에너지가 낮으면: 빌드업(low→mid→high→차분)
    # 첫 곡 에너지가 높으면: 유지→피크→차분(high→mid→low)
    first_energy_rank = sum(1 for r in remaining if r['energy'] < first['energy'])
    if first_energy_rank < n / 2:
        tail = mid + high + list(reversed(low))
    else:
        tail = high + mid + list(reversed(low))

    # 그룹 내 키 유사도 미세 조정
    ordered_tail = [tail[0]] if tail else []
    pool = tail[1:]
    while pool:
        last_key = ordered_tail[-1]['key_idx']
        best = min(pool, key=lambda x: (abs(x['energy'] - ordered_tail[-1]['energy']), key_distance(last_key, x['key_idx'])))
        ordered_tail.append(best)
        pool.remove(best)

    return [first] + ordered_tail


def main():
    if len(sys.argv) < 2:
        print("사용법: python3 download.py <SUNO 플레이리스트 URL 또는 ID> [EP 폴더]")
        print("  EP 폴더 지정 시 해당 EP의 songs/ 폴더에 저장")
        print("  EP 폴더 미지정 시 ~/Downloads/에 저장")
        sys.exit(1)

    arg = sys.argv[1]
    if "playlist/" in arg:
        playlist_id = arg.rstrip("/").split("playlist/")[-1]
    else:
        playlist_id = arg

    if len(sys.argv) >= 3:
        ep_dir = os.path.abspath(sys.argv[2])
        download_dir = os.path.join(ep_dir, "songs")
    else:
        download_dir = os.path.expanduser("~/Downloads")

    token = get_token_from_safari()
    if not token:
        print("⚠️  토큰을 가져올 수 없습니다. Safari에서 suno.com이 열려있는지 확인해주세요.")
        sys.exit(1)

    headers = {"Authorization": f"Bearer {token}"}

    print("🎵 플레이리스트 정보 가져오는 중...")
    songs = get_playlist_songs(playlist_id, headers)
    if not songs:
        print("❌ 곡을 찾을 수 없습니다.")
        sys.exit(1)

    print(f"📋 {len(songs)}곡 발견")
    for i, (_, title) in enumerate(songs):
        print(f"  {i+1}. {title}")
    print()

    download_wav(songs, headers, download_dir)

    song_titles = [title for _, title in songs]

    print()
    results = analyze_songs(download_dir, song_titles)

    if results and len(results) > 2:
        # 01, 02는 고정, 나머지를 시퀀싱
        fixed = results[:2]
        rest = results[2:]

        sequence = suggest_sequence(rest, 0)
        final = fixed + sequence

        print()
        print("🎶 시퀀싱 결과 (01~02 고정)")
        print("=" * 50)
        for i, track in enumerate(final):
            dur_m = int(track['duration'] // 60)
            dur_s = int(track['duration'] % 60)
            print(f"  {i+1:2d}. {track['title']}")
            print(f"      {track['bpm']:.0f}BPM | {track['key']} | energy={track['energy']:.4f} | {dur_m}:{dur_s:02d}")
        print("=" * 50)

        print()
        print("📝 파일명 트랙 번호 적용 중...")
        import re
        for i, track in enumerate(final):
            old_path = os.path.join(download_dir, f"{track['title']}.wav")
            # 원래 제목에서 앞의 번호 접두사 제거 (예: "03_프론티어 랜더스" → "프론티어 랜더스")
            clean_title = re.sub(r'^\d+_', '', track['title'])
            new_name = f"{i+1:02d}_{clean_title}.wav"
            new_path = os.path.join(download_dir, new_name)
            if os.path.exists(old_path):
                os.rename(old_path, new_path)
                print(f"  {track['title']} → {new_name}")

    print()
    print(f"🎉 완료! 저장 위치: {download_dir}")


if __name__ == "__main__":
    main()
