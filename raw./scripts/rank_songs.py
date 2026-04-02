#!/usr/bin/env python3
"""KBO 응원가 인기도 분석 → 다운로드 → MR 분리 → 템포 보정
사용법: python3 rank_songs.py <팀명> [--download N] [--out-dir 경로] [--target-bpm N]
예시:  python3 rank_songs.py 한화
       python3 rank_songs.py 한화 --download 12
       python3 rank_songs.py 한화 --download 12 --target-bpm 90"""

import subprocess
import sys
import os
import re
import hashlib
import unicodedata
import argparse
from collections import Counter

# KBO 전 구단 정보: 약칭 → (정식명, 마스코트명, 관련 키워드)
KBO_TEAMS = {
    "한화":  ("한화 이글스",   "이글스",   ["한화", "이글스", "이글"]),
    "두산":  ("두산 베어스",   "베어스",   ["두산", "베어스"]),
    "기아":  ("기아 타이거즈",  "타이거즈",  ["기아", "타이거즈", "KIA"]),
    "롯데":  ("롯데 자이언츠",  "자이언츠",  ["롯데", "자이언츠"]),
    "삼성":  ("삼성 라이온즈",  "라이온즈",  ["삼성", "라이온즈"]),
    "키움":  ("키움 히어로즈",  "히어로즈",  ["키움", "히어로즈"]),
    "LG":   ("LG 트윈스",    "트윈스",   ["LG", "엘지", "트윈스"]),
    "NC":   ("NC 다이노스",   "다이노스",  ["NC", "다이노스"]),
    "SSG":  ("SSG 랜더스",   "랜더스",   ["SSG", "랜더스"]),
    "KT":   ("KT 위즈",     "위즈",    ["KT", "위즈"]),
}

# 응원가에 흔히 쓰이는 키워드 (선수 이름 필터에서 면제)
SONG_KEYWORDS = [
    "사랑", "행복", "불타", "영원", "승리", "라인업", "안타송",
    "열광", "보아라", "우리", "하나", "외쳐", "던져", "그대",
    "클랩", "위하여", "태양", "가자", "내 사랑", "내사랑",
    "함성", "전진", "챔프", "꿈", "열정", "song", "this",
    "together", "forever", "champion", "victory", "cheer",
    "충청", "광주", "대전", "부산", "대구", "수원", "서울",
    "마산", "인천",  # 연고지명은 곡명일 확률 높음
]


def get_video_descriptions(query, max_results=10):
    """YouTube 검색 후 영상 제목+설명 반환"""
    try:
        result = subprocess.run([
            'yt-dlp', '--default-search', f'ytsearch{max_results}',
            '--print', '%(title)s\n---DESC---\n%(description)s\n===END===',
            '--no-download', '--no-playlist',
            query
        ], capture_output=True, text=True, timeout=60)
        return result.stdout
    except Exception:
        return ""


def extract_songs_from_descriptions(text):
    """타임스탬프 패턴에서 곡명 추출"""
    pattern = r'^(?:\d{1,2}:)?\d{1,2}:\d{2}\s+(.+)$'
    songs = []
    for line in text.split('\n'):
        line = line.strip()
        m = re.match(pattern, line)
        if not m:
            continue
        name = m.group(1).strip()
        name = re.sub(r'^[Nn][Oo]\.?\d+\s*', '', name)
        name = re.sub(r'\s*(Good\s*bye|goodbye|NEW).*$', '', name, flags=re.IGNORECASE)
        name = re.sub(r'^(?:\d{1,2}:)?\d{1,2}:\d{2}\s+', '', name)
        name = name.strip()
        if name and len(name) >= 2:
            songs.append(name)
    return songs


def normalize_name(name):
    """곡명 정규화 — 공백/쉼표/대소문자/특수문자 통일"""
    s = unicodedata.normalize('NFC', name.strip())
    s = s.lower()
    s = re.sub(r'[,!?~♪·]', '', s)
    s = re.sub(r'\s*(등장곡|응원가|수정\s*(전|후)|ver\.?\s*\d*).*$', '', s)
    s = re.sub(r'[(\[（].+?[)\]）]', '', s)
    s = re.sub(r'\s+', ' ', s).strip()
    return s


def make_player_filter(team_keywords):
    """선수 이름 필터 클로저 생성"""
    all_song_kw = SONG_KEYWORDS + [kw.lower() for kw in team_keywords]

    def is_player_name(name):
        clean = name.strip()
        lower = clean.lower()

        for kw in all_song_kw:
            if kw.lower() in lower:
                return False

        if re.fullmatch(r'[가-힣]{2,3}', clean):
            return True

        first = clean.split()[0]
        if re.fullmatch(r'[가-힣]{2,3}', first):
            return True

        if re.fullmatch(r'[가-힣]{4,5}', clean):
            return True

        return False

    return is_player_name


def make_other_team_filter(my_keywords):
    """타 구단 필터 클로저 생성"""
    my_lower = {kw.lower() for kw in my_keywords}
    other_keywords = []
    for abbr, (full, mascot, keywords) in KBO_TEAMS.items():
        if not any(kw.lower() in my_lower for kw in keywords):
            other_keywords.extend(keywords)

    def is_other_team(name):
        lower = name.lower()
        for kw in my_lower:
            if kw in lower:
                return False
        for kw in other_keywords:
            if kw.lower() in lower:
                return True
        return False

    return is_other_team


def is_junk(name):
    """파싱 쓰레기인지 판별"""
    if len(name) < 2:
        return True
    if name.startswith("===") or name.startswith("___") or name.startswith("---"):
        return True
    if re.fullmatch(r'[\d:.\s]+', name):
        return True
    if name.startswith("#"):
        return True
    return False


MAX_DURATION = 600  # 10분 이상 영상은 모음집으로 간주, 제외


def search_view_count(query, max_results=5):
    """YouTube 검색 후 개별 곡 영상 중 최대 조회수 + URL 반환 (10분 이상 제외)"""
    try:
        result = subprocess.run([
            'yt-dlp', '--default-search', f'ytsearch{max_results}',
            '--print', '%(view_count)s\t%(duration)s\t%(title)s\t%(webpage_url)s',
            '--no-download', '--no-playlist',
            query
        ], capture_output=True, text=True, timeout=30)

        best_views = 0
        best_title = ""
        best_url = ""
        for line in result.stdout.strip().split('\n'):
            if not line or '\t' not in line:
                continue
            parts = line.split('\t')
            try:
                views = int(parts[0])
                duration = int(parts[1]) if parts[1].isdigit() else 0
                if duration > MAX_DURATION:
                    continue
                if views > best_views:
                    best_views = views
                    best_title = parts[2] if len(parts) > 2 else ""
                    best_url = parts[3] if len(parts) > 3 else ""
            except (ValueError, IndexError):
                continue
        return best_views, best_title, best_url
    except Exception:
        return 0, "", ""


def download_audio(url, output_path):
    """YouTube URL에서 오디오만 다운로드"""
    try:
        result = subprocess.run([
            'yt-dlp', '-x', '--audio-format', 'mp3',
            '--audio-quality', '0',
            '-o', output_path,
            url
        ], capture_output=True, text=True, timeout=120)
        return result.returncode == 0
    except Exception:
        return False


def main():
    parser = argparse.ArgumentParser(description="KBO 응원가 인기도 분석")
    parser.add_argument("team", help=f"팀명: {', '.join(KBO_TEAMS.keys())}")
    parser.add_argument("--download", type=int, metavar="N",
                        help="상위 N곡 원곡 오디오 다운로드")
    parser.add_argument("--out-dir", metavar="경로",
                        help="다운로드 저장 경로 (기본: ~/Downloads/<팀명>_원곡)")
    parser.add_argument("--target-bpm", type=int, default=90, metavar="N",
                        help="MR 템포 보정 목표 BPM (기본: 90, 이미 이하인 곡은 유지)")
    args = parser.parse_args()

    team_key = args.team.upper() if args.team.upper() in KBO_TEAMS else args.team
    if team_key not in KBO_TEAMS:
        print(f"❌ '{team_key}' 팀을 찾을 수 없습니다.")
        print(f"사용 가능: {', '.join(KBO_TEAMS.keys())}")
        sys.exit(1)

    full_name, mascot, my_keywords = KBO_TEAMS[team_key]
    is_player = make_player_filter(my_keywords)
    is_other_team = make_other_team_filter(my_keywords)

    print("=" * 60)
    print(f"⚾ {full_name} 팀 응원가 인기도 분석")
    print("=" * 60)

    # 1단계: 플레이리스트/모음 영상에서 곡명 자동 수집
    print("\n📊 1단계: 응원가 모음 영상에서 곡명 수집 중...")
    search_queries = [
        f"{full_name} 응원가 모음 2026",
        f"{full_name} 응원가 모음 2025",
        f"{full_name} 응원가 플레이리스트 2026",
        f"{full_name} 응원가 플레이리스트 2025",
        f"{full_name} 등장곡 응원가 2026",
        f"{full_name} 팀응원가 모음",
        f"{full_name} 응원가 전곡",
    ]

    raw_songs = []
    for q in search_queries:
        print(f"  검색: {q}")
        text = get_video_descriptions(q, max_results=5)
        songs = extract_songs_from_descriptions(text)
        raw_songs.extend(songs)
        print(f"    → {len(songs)}개 항목 추출")

    print(f"\n  원본: {len(raw_songs)}개 항목")

    # 필터링
    filtered = []
    removed = {"player": 0, "other_team": 0, "junk": 0}
    for name in raw_songs:
        if is_junk(name):
            removed["junk"] += 1
        elif is_other_team(name):
            removed["other_team"] += 1
        elif is_player(name):
            removed["player"] += 1
        else:
            filtered.append(name)

    print(f"  제거: 선수 {removed['player']}개, "
          f"타구단 {removed['other_team']}개, "
          f"기타 {removed['junk']}개")
    print(f"  남은 팀 응원가: {len(filtered)}개 항목")

    # 정규화 후 카운트 (유사 곡명 병합)
    normalized_counter = Counter()
    norm_to_display = {}
    for name in filtered:
        norm = normalize_name(name)
        if not norm or len(norm) < 2:
            continue
        normalized_counter[norm] += 1
        if norm not in norm_to_display or len(name) > len(norm_to_display[norm]):
            norm_to_display[norm] = name

    print(f"  병합 후: {len(normalized_counter)}개 고유 곡")

    print(f"\n{'─' * 50}")
    print(f"  {'곡명':30s} 출현횟수")
    print(f"{'─' * 50}")
    for norm, count in normalized_counter.most_common():
        display = norm_to_display[norm]
        print(f"  {display:30s} {count}회")

    # 2단계: 상위 곡들 조회수 검색
    TOP_N = min(20, len(normalized_counter))
    top_norms = [n for n, _ in normalized_counter.most_common(TOP_N)]

    print(f"\n{'=' * 60}")
    print(f"📊 2단계: 상위 {TOP_N}곡 YouTube 조회수 수집 중...")
    print(f"{'=' * 60}")

    view_data = {}
    for norm in top_norms:
        display = norm_to_display[norm]
        query = f"{full_name} {display} 응원가"
        views, title, url = search_view_count(query)
        view_data[norm] = (views, title, url)
        views_str = f"{views:,}" if views > 0 else "N/A"
        print(f"  {display:30s} → {views_str:>12s}  ({title[:40]})")

    # URL 중복 감지: 같은 영상으로 연결된 곡들 병합
    url_to_norms = {}
    for norm in top_norms:
        url = view_data[norm][2]
        if url:
            url_to_norms.setdefault(url, []).append(norm)

    dupes_by_url = {url: norms for url, norms in url_to_norms.items() if len(norms) > 1}
    removed_norms = set()
    if dupes_by_url:
        print(f"\n⚠️  URL 중복 감지 (같은 영상으로 연결된 곡):")
        for url, norms in dupes_by_url.items():
            displays = [norm_to_display[n] for n in norms]
            # 출현빈도 가장 높은 것을 대표로, 나머지 제거
            norms_sorted = sorted(norms, key=lambda n: normalized_counter[n], reverse=True)
            keep = norms_sorted[0]
            remove = norms_sorted[1:]
            # 제거 대상의 출현빈도를 대표에 합산
            for r in remove:
                normalized_counter[keep] += normalized_counter[r]
                removed_norms.add(r)
            print(f"  {' / '.join(displays)} → '{norm_to_display[keep]}' 유지 (나머지 병합)")

    # 중복 제거된 목록
    deduped_norms = [n for n in top_norms if n not in removed_norms]

    # 3단계: 종합 점수
    print(f"\n{'=' * 60}")
    print("🏆 종합 순위 (출현빈도 50% + 조회수 50%)")
    print(f"{'=' * 60}")

    max_count = max(normalized_counter[n] for n in deduped_norms) or 1
    views_list = [view_data[n][0] for n in deduped_norms]
    max_views = max(views_list) if views_list and max(views_list) > 0 else 1

    scores = {}
    for norm in deduped_norms:
        count_score = normalized_counter[norm] / max_count
        view_score = view_data[norm][0] / max_views
        combined = count_score * 0.5 + view_score * 0.5
        scores[norm] = (combined, normalized_counter[norm], view_data[norm][0])

    ranked = sorted(scores.items(), key=lambda x: x[1][0], reverse=True)

    for i, (norm, (score, count, views)) in enumerate(ranked, 1):
        display = norm_to_display[norm]
        bar = "█" * int(score * 20)
        views_str = f"{views:,}" if views > 0 else "N/A"
        print(f"  {i:2d}. {display:30s}  점수: {score:.2f}  {bar}")
        print(f"      출현: {count}회  |  조회수: {views_str:>12s}")

    print(f"\n{'=' * 60}")
    print(f"💡 상위 10~12곡을 플레이리스트에 넣는 것을 추천합니다")
    print(f"{'=' * 60}")

    # 4단계: 다운로드 (옵션)
    if args.download:
        dl_count = min(args.download, len(ranked))
        out_dir = args.out_dir or os.path.expanduser(f"~/Downloads/{team_key}_원곡")
        os.makedirs(out_dir, exist_ok=True)

        MAX_SOURCES = 5  # 최대 소스 영상 시도 횟수

        print(f"\n{'=' * 60}")
        print(f"📥 모음 영상에서 곡별 커팅 다운로드 → {out_dir}")
        print(f"{'=' * 60}")

        # 소스 후보 수집: 최신+조회수 순
        print("\n  소스 영상 탐색 중...")
        source_queries = [
            f"{full_name} 응원가 모음 2026",
            f"{full_name} 응원가 모음 2025",
            f"{full_name} 응원가 플레이리스트 2026",
            f"{full_name} 팀응원가 모음",
        ]

        SEP = "|||"
        sources = []  # [(url, title, views, upload_date, tracks)]
        seen_urls = set()
        for q in source_queries:
            try:
                result = subprocess.run([
                    'yt-dlp', '--default-search', 'ytsearch5',
                    '--print',
                    f'%(webpage_url)s{SEP}%(view_count)s{SEP}%(upload_date)s{SEP}%(title)s{SEP}%(description)s{SEP}END',
                    '--no-download', '--no-playlist', q
                ], capture_output=True, text=True, timeout=60)

                for record in result.stdout.split(f"{SEP}END"):
                    record = record.strip()
                    if not record:
                        continue
                    parts = record.split(SEP, 4)
                    if len(parts) < 5:
                        continue
                    url, views_str, date, title, desc = [p.strip() for p in parts]
                    if url in seen_urls or not url.startswith("http"):
                        continue
                    seen_urls.add(url)
                    views = int(views_str) if views_str.isdigit() else 0

                    tracks = []
                    for line in desc.split('\n'):
                        line = line.strip()
                        m = re.match(r'^((?:\d{1,2}:)?\d{1,2}:\d{2})\s+(.+)$', line)
                        if not m:
                            continue
                        ts, name = m.group(1), m.group(2).strip()
                        name = re.sub(r'^[Nn][Oo]\.?\d+\s*', '', name).strip()
                        if name and len(name) >= 2:
                            tracks.append({"name": name, "start": ts})

                    if len(tracks) >= 3:
                        sources.append((url, title, views, date or "", tracks))
            except Exception:
                continue

        sources.sort(key=lambda s: (s[3], s[2]), reverse=True)

        if not sources:
            print("  ❌ 타임스탬프가 있는 모음 영상을 찾을 수 없습니다")
            sys.exit(1)

        print(f"  {len(sources)}개 소스 영상 발견 (최대 {MAX_SOURCES}개 사용)")

        def match_track(display_name, tracks):
            dn = normalize_name(display_name)
            for i, t in enumerate(tracks):
                tn = normalize_name(t["name"])
                if dn == tn or dn in tn or tn in dn:
                    return i
            return None

        # 폴백 체인: 소스 순회하며 곡 채우기
        remaining = {}
        for norm, _ in ranked[:dl_count]:
            remaining[norm] = norm_to_display[norm]
        downloaded = {}  # norm → (display, tmp_path)

        for src_i, (src_url, src_title, src_views, src_date, src_tracks) in enumerate(sources[:MAX_SOURCES]):
            if not remaining:
                break

            # 매칭 가능한 곡 확인
            matchable = []
            for norm, display in remaining.items():
                idx = match_track(display, src_tracks)
                if idx is not None:
                    matchable.append((norm, display, idx))

            if not matchable:
                continue

            y, m = src_date[:4], src_date[4:6]
            print(f"\n  📀 소스 {src_i+1}: [{y}-{m}] {src_title[:50]}")
            print(f"     {len(matchable)}곡 매칭, 오디오 다운로드 중...")

            src_audio = os.path.join(out_dir, f"_source_{src_i}.mp3")
            download_audio(src_url, os.path.join(out_dir, f"_source_{src_i}.%(ext)s"))

            for norm, display, idx in matchable:
                start = src_tracks[idx]["start"]
                if idx + 1 < len(src_tracks):
                    end = src_tracks[idx + 1]["start"]
                    duration_args = ['-to', end]
                else:
                    duration_args = []

                safe_name = re.sub(r'[/:*?"<>|]', '_', display)
                tmp_path = os.path.join(out_dir, f"_tmp_{safe_name}.mp3")

                cut = subprocess.run([
                    'ffmpeg', '-y', '-i', src_audio,
                    '-ss', start, *duration_args,
                    '-acodec', 'libmp3lame', '-q:a', '0',
                    '-v', 'quiet', tmp_path
                ])

                if cut.returncode == 0:
                    downloaded[norm] = (display, tmp_path)
                    del remaining[norm]
                    print(f"     ✅ {display:30s} [{start}{' → ' + end if duration_args else ''}]")
                else:
                    print(f"     ❌ {display:30s} 커팅 실패")

            if os.path.exists(src_audio):
                os.remove(src_audio)

        if remaining:
            print(f"\n  ⚠️  {MAX_SOURCES}개 소스에서 못 찾은 곡 ({len(remaining)}곡, 제외):")
            for display in remaining.values():
                print(f"     - {display}")

        # 랭킹 순서로 넘버링
        file_num = 1
        for norm, _ in ranked[:dl_count]:
            if norm not in downloaded:
                continue
            display, tmp_path = downloaded[norm]
            if not os.path.exists(tmp_path):
                continue
            safe_name = re.sub(r'[/:*?"<>|]', '_', display)
            final_path = os.path.join(out_dir, f"{file_num:02d}_{safe_name}.mp3")
            os.rename(tmp_path, final_path)
            file_num += 1

        # 남은 임시 파일 정리
        for f in os.listdir(out_dir):
            if f.startswith('_'):
                os.remove(os.path.join(out_dir, f))

        final_files = sorted(f for f in os.listdir(out_dir) if f.endswith('.mp3'))
        print(f"\n✅ 다운로드 완료: {out_dir} ({len(final_files)}곡)")
        for f in final_files:
            print(f"  {f}")

        # 5단계: MR 분리 (demucs)
        mr_dir = out_dir.rstrip('/') + '_MR'
        demucs_dir = out_dir.rstrip('/') + '_demucs'
        os.makedirs(mr_dir, exist_ok=True)

        print(f"\n{'=' * 60}")
        print(f"🎵 MR 분리 (보컬 제거) → {mr_dir}")
        print(f"{'=' * 60}")

        demucs_result = subprocess.run([
            'demucs', '--two-stems=vocals', '-n', 'htdemucs', '--mp3',
            '-o', demucs_dir
        ] + [os.path.join(out_dir, f) for f in final_files],
            capture_output=True, text=True, timeout=1800)

        if demucs_result.returncode != 0:
            print(f"  ❌ demucs 실패: {demucs_result.stderr[:200]}")
        else:
            for f in final_files:
                stem_name = os.path.splitext(f)[0]
                src = os.path.join(demucs_dir, 'htdemucs', stem_name, 'no_vocals.mp3')
                if os.path.exists(src):
                    import shutil
                    shutil.copy2(src, os.path.join(mr_dir, f))
                    print(f"  ✅ {f}")
                else:
                    print(f"  ❌ {f} — MR 파일 없음")

            # demucs 임시 폴더 삭제
            import shutil
            shutil.rmtree(demucs_dir, ignore_errors=True)

        # 6단계: 템포 보정
        mr_files = sorted(f for f in os.listdir(mr_dir) if f.endswith('.mp3'))
        if mr_files:
            target_bpm = args.target_bpm
            slow_dir = mr_dir.rstrip('/') + '_slow'
            os.makedirs(slow_dir, exist_ok=True)

            print(f"\n{'=' * 60}")
            print(f"🎶 템포 보정 (목표 {target_bpm} BPM) → {slow_dir}")
            print(f"{'=' * 60}")

            import librosa
            import shutil as _shutil

            for f in mr_files:
                path = os.path.join(mr_dir, f)
                # 원곡 BPM 기준으로 감지 (MR보다 안정적)
                orig_path = os.path.join(out_dir, f)
                bpm_src = orig_path if os.path.exists(orig_path) else path
                y, sr = librosa.load(bpm_src, sr=None)
                tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
                bpm = float(tempo[0]) if hasattr(tempo, '__len__') else float(tempo)
                name = f[3:-4]
                out_path = os.path.join(slow_dir, f)
                src_label = "원곡" if bpm_src == orig_path else "MR"

                if bpm <= target_bpm:
                    _shutil.copy2(path, out_path)
                    print(f"  {f[:2]}. {name:30s} {bpm:5.0f} BPM [{src_label}]  (유지)")
                    continue

                ratio = target_bpm / bpm
                filters = []
                r = ratio
                while r < 0.5:
                    filters.append('atempo=0.5')
                    r /= 0.5
                while r > 2.0:
                    filters.append('atempo=2.0')
                    r /= 2.0
                filters.append(f'atempo={r:.4f}')
                af = ','.join(filters)

                subprocess.run([
                    'ffmpeg', '-y', '-i', path,
                    '-filter:a', af,
                    '-q:a', '0', '-v', 'quiet', out_path
                ])
                print(f"  {f[:2]}. {name:30s} {bpm:5.0f} → {target_bpm} BPM [{src_label}]  (x{ratio:.2f})")

            print(f"\n✅ 전체 완료:")
            print(f"  원곡:     {out_dir}")
            print(f"  MR:      {mr_dir}")
            print(f"  MR slow: {slow_dir}")


if __name__ == "__main__":
    main()
