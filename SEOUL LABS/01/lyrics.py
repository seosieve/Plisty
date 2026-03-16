#!/usr/bin/env python3
"""SUNO 가사 싱크 데이터 자동 다운로드 스크립트
오디오 메타데이터에서 SUNO ID를 추출하고 API로 LRC 파일을 생성합니다.

사용법:
  python3 lyrics.py              # Safari에서 자동으로 토큰 읽기
  python3 lyrics.py --token "토큰"  # 수동으로 토큰 전달
"""

import json
import os
import re
import subprocess
import sys
import argparse
import urllib.request

SONGS_DIR = os.path.join(os.path.dirname(__file__), "songs")
LYRICS_DIR = os.path.join(os.path.dirname(__file__), "lyrics")
API_BASE = "https://studio-api.prod.suno.com/api/gen/{}/aligned_lyrics/v2/"


def get_token_from_safari():
    """Safari에서 SUNO __session 쿠키 자동 추출"""
    try:
        result = subprocess.run(
            ["osascript", "-e",
             'tell application "Safari" to do JavaScript "document.cookie" in current tab of front window'],
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


def get_suno_id(mp3_path):
    """MP3 메타데이터에서 SUNO ID 추출"""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format_tags", mp3_path],
        capture_output=True, text=True
    )
    for line in result.stdout.splitlines():
        if "id=" in line and "made with suno" in line:
            for part in line.split(";"):
                part = part.strip()
                if part.startswith("id="):
                    return part[3:]
    return None


def fetch_aligned_lyrics(suno_id, token):
    """SUNO API에서 가사 싱크 데이터 가져오기"""
    url = API_BASE.format(suno_id)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print(f"  [오류] 인증 실패 (HTTP {e.code}) - 쿠키가 만료되었습니다. 새 __session 값을 입력해주세요.")
        else:
            print(f"  [오류] HTTP {e.code}: {e.reason}")
        return None


def to_lrc(aligned_words):
    """aligned_words JSON → LRC 포맷 변환 (라인 단위)"""
    lines = []
    current_line = ""
    current_start = None

    for w in aligned_words:
        word = w["word"]
        start = w["start_s"]

        if current_start is None:
            current_start = start

        if word.endswith("\n"):
            current_line += word.rstrip("\n")
            if current_line.strip():
                m = int(current_start // 60)
                s = current_start % 60
                lines.append(f"[{m:02d}:{s:05.2f}]{current_line.strip()}")
            current_line = ""
            current_start = None
        else:
            current_line += word

    if current_line.strip() and current_start is not None:
        m = int(current_start // 60)
        s = current_start % 60
        lines.append(f"[{m:02d}:{s:05.2f}]{current_line.strip()}")

    return "\n".join(lines)


def to_srt(aligned_words):
    """aligned_words JSON → SRT 포맷 변환 (라인 단위)"""
    entries = []
    current_line = ""
    current_start = None
    current_end = None
    idx = 1

    for w in aligned_words:
        word = w["word"]
        start = w["start_s"]
        end = w["end_s"]

        if current_start is None:
            current_start = start
        current_end = end

        if word.endswith("\n"):
            current_line += word.rstrip("\n")
            if current_line.strip():
                entries.append(format_srt_entry(idx, current_start, current_end, current_line.strip()))
                idx += 1
            current_line = ""
            current_start = None
            current_end = None
        else:
            current_line += word

    if current_line.strip() and current_start is not None:
        entries.append(format_srt_entry(idx, current_start, current_end, current_line.strip()))

    return "\n".join(entries)


def format_srt_entry(idx, start, end, text):
    def fmt(t):
        h = int(t // 3600)
        m = int((t % 3600) // 60)
        s = int(t % 60)
        ms = int((t % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    return f"{idx}\n{fmt(start)} --> {fmt(end)}\n{text}\n"


def main():
    parser = argparse.ArgumentParser(description="SUNO 가사 싱크 다운로더")
    parser.add_argument("--token", required=False, help="SUNO __session 쿠키값 (생략 시 Safari에서 자동 추출)")
    parser.add_argument("--format", choices=["lrc", "srt", "both"], default="both", help="출력 포맷 (기본: both)")
    args = parser.parse_args()

    if not args.token:
        print("🔑 Safari에서 SUNO 토큰 자동 추출 중...")
        args.token = get_token_from_safari()
        if args.token:
            print("  ✅ 토큰 확인 완료")
        else:
            print("  ❌ Safari에서 토큰을 읽을 수 없습니다.")
            print("  Safari에서 suno.com이 열려있는지 확인하거나, --token 옵션을 사용해주세요.")
            return

    os.makedirs(LYRICS_DIR, exist_ok=True)

    AUDIO_EXTS = (".mp3", ".wav", ".flac", ".m4a")
    mp3_files = sorted([f for f in os.listdir(SONGS_DIR) if f.lower().endswith(AUDIO_EXTS)])

    if not mp3_files:
        print("songs/ 폴더에 오디오 파일이 없습니다.")
        return

    print(f"총 {len(mp3_files)}곡 발견\n")

    for mp3 in mp3_files:
        name = os.path.splitext(mp3)[0]
        mp3_path = os.path.join(SONGS_DIR, mp3)
        print(f"[{name}]")

        suno_id = get_suno_id(mp3_path)
        if not suno_id:
            print("  SUNO ID를 찾을 수 없습니다. 스킵.")
            continue
        print(f"  ID: {suno_id}")

        data = fetch_aligned_lyrics(suno_id, args.token)
        if not data or "aligned_words" not in data:
            print("  가사 데이터를 가져올 수 없습니다.")
            continue

        words = data["aligned_words"]
        # 대괄호 태그 제거 — 태그가 여러 word에 걸쳐 쪼개질 수 있으므로
        # 전체 텍스트를 합쳐서 한 번에 제거 후, 글자 위치로 역추적
        full = ''.join(w["word"] for w in words)
        # 태그 위치 수집 (삭제할 글자 인덱스 집합)
        remove_indices = set()
        for m in re.finditer(r'\[.*?\]', full, flags=re.DOTALL):
            for i in range(m.start(), m.end()):
                remove_indices.add(i)
        # 각 word에서 해당 인덱스의 글자 제거
        pos = 0
        for w in words:
            cleaned = []
            for ch in w["word"]:
                if pos not in remove_indices:
                    cleaned.append(ch)
                pos += 1
            w["word"] = ''.join(cleaned)
        data["aligned_words"] = [w for w in words if w["word"].strip()]
        print(f"  {len(words)}개 단어 싱크 데이터 수신")

        if args.format in ("lrc", "both"):
            lrc = to_lrc(words)
            lrc_path = os.path.join(LYRICS_DIR, f"{name}.lrc")
            with open(lrc_path, "w", encoding="utf-8") as f:
                f.write(lrc)
            print(f"  저장: {lrc_path}")

        if args.format in ("srt", "both"):
            srt = to_srt(words)
            srt_path = os.path.join(LYRICS_DIR, f"{name}.srt")
            with open(srt_path, "w", encoding="utf-8") as f:
                f.write(srt)
            print(f"  저장: {srt_path}")

        # raw JSON도 저장 (나중에 word-level 활용 가능)
        json_path = os.path.join(LYRICS_DIR, f"{name}.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        print()

    print("완료!")


if __name__ == "__main__":
    main()
