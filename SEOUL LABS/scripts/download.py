#!/usr/bin/env python3
"""SUNO 플레이리스트에서 wav 파일 일괄 다운로드 + 오디오 분석/시퀀싱 추천"""

import sys
import os
import requests
import time

import numpy as np
import librosa

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)
from lyrics import get_token_from_safari

BASE = "https://studio-api.prod.suno.com"
KEYS = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']


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


def analyze_songs(download_dir):
    """다운로드된 곡들의 오디오 특성 분석"""
    wav_files = sorted([f for f in os.listdir(download_dir) if f.endswith('.wav')])
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


def suggest_sequence(results):
    """에너지 커브 기반 시퀀싱 추천 (중→고→저 아크)"""
    if len(results) <= 2:
        return results

    sorted_by_energy = sorted(results, key=lambda x: x['energy'])
    n = len(sorted_by_energy)

    # 에너지 순으로 정렬 후, 중간 → 높은 → 낮은 아크 배치
    low = sorted_by_energy[:n // 3]
    mid = sorted_by_energy[n // 3: 2 * n // 3]
    high = sorted_by_energy[2 * n // 3:]

    # 아크: mid → high → low (시작-중간에너지, 중반-하이, 끝-차분)
    sequence = mid + high + list(reversed(low))

    # 인접 곡 간 키 유사도로 미세 조정 (같은 그룹 내에서)
    def key_distance(k1, k2):
        d = abs(k1 - k2)
        return min(d, 12 - d)

    for group_start, group_end in [(0, len(mid)), (len(mid), len(mid) + len(high)), (len(mid) + len(high), len(sequence))]:
        group = sequence[group_start:group_end]
        if len(group) <= 1:
            continue
        # 그리디: 현재 곡에서 키가 가장 가까운 다음 곡 선택
        ordered = [group[0]]
        remaining = group[1:]
        while remaining:
            last_key = ordered[-1]['key_idx']
            best = min(remaining, key=lambda x: key_distance(last_key, x['key_idx']))
            ordered.append(best)
            remaining.remove(best)
        sequence[group_start:group_end] = ordered

    return sequence


def main():
    if len(sys.argv) < 2:
        print("사용법: python download.py <SUNO 플레이리스트 URL 또는 ID> [EP 폴더]")
        print("  EP 폴더 지정 시 해당 EP의 songs/ 폴더에 저장")
        print("  EP 폴더 미지정 시 ~/Downloads/에 저장")
        sys.exit(1)

    arg = sys.argv[1]
    # URL에서 playlist ID 추출
    if "playlist/" in arg:
        playlist_id = arg.rstrip("/").split("playlist/")[-1]
    else:
        playlist_id = arg

    # 저장 경로 결정
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

    print()
    results = analyze_songs(download_dir)

    if results:
        sequence = suggest_sequence(results)
        print()
        print("🎶 추천 시퀀싱 (에너지 커브: 중→고→저)")
        print("=" * 50)
        for i, track in enumerate(sequence):
            dur_m = int(track['duration'] // 60)
            dur_s = int(track['duration'] % 60)
            print(f"  {i+1:2d}. {track['title']}")
            print(f"      {track['bpm']:.0f}BPM | {track['key']} | energy={track['energy']:.4f} | {dur_m}:{dur_s:02d}")
        print("=" * 50)

        # 파일명에 트랙 번호 접두사 붙이기
        print()
        print("📝 파일명 트랙 번호 적용 중...")
        for i, track in enumerate(sequence):
            old_path = os.path.join(download_dir, f"{track['title']}.wav")
            new_path = os.path.join(download_dir, f"{i+1:02d}_{track['title']}.wav")
            if os.path.exists(old_path):
                os.rename(old_path, new_path)
                print(f"  {track['title']} → {i+1:02d}_{track['title']}.wav")

    print()
    print(f"🎉 완료! 저장 위치: {download_dir}")


if __name__ == "__main__":
    main()
