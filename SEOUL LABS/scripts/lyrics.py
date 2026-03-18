#!/usr/bin/env python3
"""SUNO 가사 싱크 + stable-ts forced alignment 보정

generate.py에서 import해서 사용하거나, 단독 실행 가능:
  python3 lyrics.py <EP 폴더 경로>
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import shutil
import urllib.request

SUNO_API_BASE = "https://studio-api.prod.suno.com/api/gen/{}/aligned_lyrics/v2/"
AUDIO_EXTENSIONS = {'.wav', '.mp3', '.flac', '.m4a', '.aac'}
MIN_SEC_PER_WORD = 0.2  # 단어당 최소 초 (이하면 비정상)
ALIGNMENT_OFFSET = 0.3  # alignment 결과에 더할 오프셋 (초)


# ============================================================
# SUNO API
# ============================================================
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


def get_suno_id(filepath):
    """오디오 메타데이터에서 SUNO ID 추출"""
    result = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format_tags", filepath],
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
    url = SUNO_API_BASE.format(suno_id)
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print(f"  [오류] 인증 실패 (HTTP {e.code}) - 쿠키가 만료되었습니다.")
        else:
            print(f"  [오류] HTTP {e.code}: {e.reason}")
        return None


def remove_bracket_tags(words):
    """대괄호 태그 제거 ([Verse], [Chorus] 등)"""
    full = ''.join(w["word"] for w in words)
    remove_indices = set()
    for m in re.finditer(r'\[.*?\]', full, flags=re.DOTALL):
        for i in range(m.start(), m.end()):
            remove_indices.add(i)
    pos = 0
    for w in words:
        cleaned = []
        for ch in w["word"]:
            if pos not in remove_indices:
                cleaned.append(ch)
            pos += 1
        w["word"] = ''.join(cleaned)
    return [w for w in words if w["word"].strip()]


# ============================================================
# 가사 싱크
# ============================================================
def sync_lyrics(songs_dir, lyrics_dir, song_files):
    """songs/ 폴더와 lyrics/ 폴더를 자동 싱크"""
    os.makedirs(lyrics_dir, exist_ok=True)

    song_names = {os.path.splitext(f)[0] for f in song_files}
    existing_lyrics = {
        os.path.splitext(f)[0]
        for f in os.listdir(lyrics_dir) if f.endswith('.json')
    } if os.path.isdir(lyrics_dir) else set()

    # 삭제된 곡의 가사 정리
    removed = existing_lyrics - song_names
    for name in removed:
        for ext in ('.json', '.lrc', '.srt'):
            path = os.path.join(lyrics_dir, f"{name}{ext}")
            if os.path.exists(path):
                os.remove(path)
                print(f"  🗑  가사 삭제: {name}{ext}")

    # 새 곡의 가사 다운로드
    missing = song_names - existing_lyrics
    if missing:
        print("  🔑 Safari에서 SUNO 토큰 추출 중...")
        token = get_token_from_safari()
        if not token:
            print("  ⚠️  토큰을 가져올 수 없습니다. Safari에서 suno.com이 열려있는지 확인해주세요.")
            print("  가사 없이 영상을 생성합니다.")
        else:
            for song_file in song_files:
                name = os.path.splitext(song_file)[0]
                if name not in missing:
                    continue

                filepath = os.path.join(songs_dir, song_file)
                print(f"  🎤 가사 다운로드: {name}")

                suno_id = get_suno_id(filepath)
                if not suno_id:
                    print(f"    SUNO ID를 찾을 수 없습니다. 스킵.")
                    continue

                data = fetch_aligned_lyrics(suno_id, token)
                if not data or "aligned_words" not in data:
                    print(f"    가사 데이터를 가져올 수 없습니다.")
                    continue

                data["aligned_words"] = remove_bracket_tags(data["aligned_words"])

                json_path = os.path.join(lyrics_dir, f"{name}.json")
                with open(json_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                print(f"    ✅ 저장 완료 ({len(data['aligned_words'])}개 단어)")
    else:
        print("  ✅ 가사 싱크 완료 (변경 없음)")

    # forced alignment 보정 (항상 실행)
    for song_file in song_files:
        name = os.path.splitext(song_file)[0]
        json_path = os.path.join(lyrics_dir, f"{name}.json")
        if os.path.exists(json_path):
            filepath = os.path.join(songs_dir, song_file)
            fix_timestamps_with_alignment(filepath, json_path)


# ============================================================
# Forced Alignment 타임스탬프 보정 (stable-ts)
# ============================================================
def build_lines_from_words(words):
    """aligned_words를 줄 단위로 묶어서 반환"""
    lines = []
    line_words = []
    line_start_idx = 0

    for i, w in enumerate(words):
        line_words.append(w)
        if w['word'].endswith('\n') or i == len(words) - 1:
            text = ''.join(wd['word'] for wd in line_words).strip()
            word_count = len(text.split())
            start_s = line_words[0]['start_s']
            end_s = line_words[-1]['end_s']
            duration = end_s - start_s
            lines.append({
                'text': text,
                'start_s': start_s,
                'end_s': end_s,
                'duration': duration,
                'word_count': word_count,
                'sec_per_word': duration / word_count if word_count > 0 else 0,
                'word_start_idx': line_start_idx,
                'word_end_idx': i,
            })
            line_words = []
            line_start_idx = i + 1

    return lines


def _alpha_only(s):
    return re.sub(r'[^a-z가-힣]', '', s.lower())


def _detect_language(text):
    """텍스트에 한국어가 포함되어 있으면 'ko', 아니면 'en'"""
    if any('\uac00' <= ch <= '\ud7a3' for ch in text):
        return 'ko'
    return 'en'


def _run_alignment(audio_path, text, temp_dir, name='audio'):
    """stable-ts forced alignment 실행, align_words 리스트 반환"""
    text_path = os.path.join(temp_dir, f'{name}.txt')
    with open(text_path, 'w', encoding='utf-8') as f:
        f.write(text)

    align_name = os.path.splitext(os.path.basename(audio_path))[0]
    lang = _detect_language(text)

    subprocess.run([
        'stable-ts', audio_path,
        '--model', 'base',
        '--language', lang,
        '--align', text_path,
        '--output_format', 'json',
        '--output_dir', temp_dir,
    ], capture_output=True, text=True)

    align_json = os.path.join(temp_dir, f'{align_name}.json')
    if not os.path.exists(align_json):
        return []

    with open(align_json, 'r', encoding='utf-8') as f:
        align_data = json.load(f)
    # 읽은 후 삭제 (같은 이름으로 재실행 시 overwrite 프롬프트 방지)
    os.remove(align_json)

    align_words = []
    for seg in align_data.get('segments', []):
        for w in seg.get('words', []):
            align_words.append({
                'word': w['word'].strip().lower(),
                'start_s': w['start'],
                'end_s': w['end'],
            })
    return align_words


def _apply_alignment_to_words(words, align_words):
    """alignment 결과를 words에 char-level 매칭으로 적용, 매칭 수 반환"""
    align_chars = []
    for aw in align_words:
        alphas = _alpha_only(aw['word'])
        if not alphas:
            continue
        dur = aw['end_s'] - aw['start_s']
        char_dur = dur / len(alphas) if alphas else 0
        for ci, ch in enumerate(alphas):
            align_chars.append((
                ch,
                aw['start_s'] + ci * char_dur,
                aw['start_s'] + (ci + 1) * char_dur,
            ))

    ac_cursor = 0
    matched = 0
    for w in words:
        suno_alpha = _alpha_only(w['word'])
        if not suno_alpha:
            continue
        first_time = None
        last_time = None
        all_found = True
        temp_cursor = ac_cursor
        for ch in suno_alpha:
            found = False
            for ci in range(temp_cursor, len(align_chars)):
                if align_chars[ci][0] == ch:
                    if first_time is None:
                        first_time = align_chars[ci][1]
                    last_time = align_chars[ci][2]
                    temp_cursor = ci + 1
                    found = True
                    break
            if not found:
                all_found = False
                break
        if all_found and first_time is not None:
            w['start_s'] = first_time
            w['end_s'] = last_time
            ac_cursor = temp_cursor
            matched += 1
    return matched


def _fix_problem_regions(words, all_lines, wav_path, temp_dir, depth=0, max_depth=3):
    """0초 duration 줄을 region 단위로 재alignment (재귀)"""
    if depth >= max_depth:
        return

    def _has_zero_word(line):
        """줄 내에 0-duration 단어가 있는지 확인"""
        for wi in range(line['word_start_idx'], line['word_end_idx'] + 1):
            w = words[wi]
            alpha = _alpha_only(w['word'])
            if alpha and w['end_s'] - w['start_s'] <= 0.02:
                return True
        return False

    zero_lines = [(i, l) for i, l in enumerate(all_lines)
                  if l['word_count'] > 0 and (l['duration'] <= 0.05 or _has_zero_word(l))]
    if not zero_lines:
        return

    label = f"{'  ' * depth}2단계" if depth == 0 else f"{'  ' * depth}재보정({depth+1}차)"
    print(f"  🔧 {label}: {len(zero_lines)}개 문제 구간 재alignment...")

    fixed_indices = set()
    for zl_idx, zl in zero_lines:
        # 앞뒤 1줄 포함 context
        ctx_start = max(0, zl_idx - 1)
        ctx_end = min(len(all_lines), zl_idx + 2)
        ctx_lines = all_lines[ctx_start:ctx_end]

        margin = 1.0
        cut_start = max(0, ctx_lines[0]['start_s'] - margin)
        cut_end = ctx_lines[-1]['end_s'] + margin

        cut_name = f'fix_d{depth}_{zl_idx}'
        cut_path = os.path.join(temp_dir, f'{cut_name}.wav')
        subprocess.run([
            'ffmpeg', '-y', '-i', wav_path,
            '-ss', str(cut_start), '-to', str(cut_end),
            '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', cut_path
        ], capture_output=True)

        ctx_text = '\n'.join(l['text'] for l in ctx_lines)
        region_align = _run_alignment(cut_path, ctx_text, temp_dir, cut_name)
        if not region_align:
            print(f"    ⚠️  재alignment 실패: \"{zl['text'][:40]}\"")
            continue

        # 오프셋 보정 (cut_start 기준)
        for aw in region_align:
            aw['start_s'] += cut_start
            aw['end_s'] += cut_start

        # context 전체 단어를 교체 (앞뒤 줄 포함)
        region_word_start = ctx_lines[0]['word_start_idx']
        region_word_end = ctx_lines[-1]['word_end_idx']
        region_words = words[region_word_start:region_word_end + 1]

        old_dur = zl['end_s'] - zl['start_s']
        _apply_alignment_to_words(region_words, region_align)

        # 줄 단위로 다시 계산
        new_zl_words = words[zl['word_start_idx']:zl['word_end_idx'] + 1]
        new_line_dur = new_zl_words[-1]['end_s'] - new_zl_words[0]['start_s']

        if new_line_dur > 0.05:
            print(f"    ✅ \"{zl['text'][:40]}\" ({old_dur:.1f}s → {new_line_dur:.1f}s)")
            for li in range(ctx_start, ctx_end):
                fixed_indices.add(li)
        else:
            print(f"    ⚠️  여전히 문제: \"{zl['text'][:40]}\"")

    if fixed_indices:
        # 교체 후 줄 다시 빌드하고 겹침 확인
        new_lines = build_lines_from_words(words)
        # 겹침 검사: 현재 줄 시작 < 이전 줄 끝
        overlap_lines = []
        for i in range(1, len(new_lines)):
            if new_lines[i]['start_s'] < new_lines[i-1]['end_s'] - 0.05:
                if new_lines[i]['duration'] <= 0.05 or new_lines[i-1]['duration'] <= 0.05:
                    overlap_lines.append(i)

        if overlap_lines:
            print(f"    ⚠️  {len(overlap_lines)}개 겹침 발견, 재보정 시도...")
            _fix_problem_regions(words, new_lines, wav_path, temp_dir, depth + 1, max_depth)


def fix_timestamps_with_alignment(audio_path, json_path):
    """전체 곡 stable-ts forced alignment + 문제 구간 재alignment"""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    words = data['aligned_words']

    full_text = ''.join(w['word'] for w in words).strip()
    if not full_text:
        return

    # === 1단계: 전체 곡 alignment ===
    print(f"  🔧 1단계: 전체 곡 forced alignment...")

    temp_dir = tempfile.mkdtemp(prefix='align_full_')
    wav_path = os.path.join(temp_dir, 'audio.wav')
    subprocess.run([
        'ffmpeg', '-y', '-i', audio_path,
        '-ar', '16000', '-ac', '1', '-c:a', 'pcm_s16le', wav_path
    ], capture_output=True)

    align_words = _run_alignment(wav_path, full_text, temp_dir)

    if not align_words:
        print(f"    ⚠️  alignment 실패")
        shutil.rmtree(temp_dir)
        return

    matched = _apply_alignment_to_words(words, align_words)
    print(f"    📊 {matched}/{len(words)}개 단어 매칭 완료")

    match_rate = matched / len(words) if words else 0
    if match_rate < 0.5:
        print(f"    ⚠️  매칭률 {match_rate:.0%}로 너무 낮음, SUNO 원본 유지")
        shutil.rmtree(temp_dir)
        return

    # === 2단계: 0초 duration 구간 재alignment ===
    all_lines = build_lines_from_words(words)
    _fix_problem_regions(words, all_lines, wav_path, temp_dir)

    shutil.rmtree(temp_dir)

    # === 3단계: 섹션 끝 줄 duration cap ===
    # 섹션 마지막 줄이 비정상적으로 길면 잘라줌 (instrumental break 붙는 현상)
    MAX_SEC_PER_WORD = 2.0
    all_lines = build_lines_from_words(words)
    for line in all_lines:
        if line['word_count'] == 0:
            continue
        last_word = words[line['word_end_idx']]
        if not last_word['word'].endswith('\n\n'):
            continue
        if line['sec_per_word'] <= MAX_SEC_PER_WORD:
            continue
        capped_dur = line['word_count'] * MAX_SEC_PER_WORD
        new_end = line['start_s'] + capped_dur
        old_end = line['end_s']
        per_word = capped_dur / line['word_count']
        for wi in range(line['word_start_idx'], line['word_end_idx'] + 1):
            idx = wi - line['word_start_idx']
            words[wi]['start_s'] = line['start_s'] + idx * per_word
            words[wi]['end_s'] = line['start_s'] + (idx + 1) * per_word
        print(f"    ✂️  \"{line['text'][:40]}\" 섹션 끝 cap: {old_end:.1f}s → {new_end:.1f}s")

    # 오프셋 적용 (섹션 첫 줄은 제외)
    if ALIGNMENT_OFFSET != 0:
        section_start = True
        for w in words:
            if not section_start:
                w['start_s'] = max(0, w['start_s'] + ALIGNMENT_OFFSET)
                w['end_s'] = max(0, w['end_s'] + ALIGNMENT_OFFSET)
            if w['word'].endswith('\n\n'):
                section_start = True
            elif w['word'].endswith('\n'):
                section_start = False

    # 줄 단위로 결과 출력
    new_lines = build_lines_from_words(words)
    for l in new_lines[:10]:
        print(f"    [{l['start_s']:6.1f}s - {l['end_s']:6.1f}s] ({l['duration']:4.1f}s) \"{l['text'][:50]}\"")
    if len(new_lines) > 10:
        print(f"    ... 외 {len(new_lines) - 10}줄")

    data['aligned_words'] = words
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ 전체 곡 alignment 적용 완료")


# ============================================================
# 가사 파싱 (generate.py에서 사용)
# ============================================================
def parse_lyrics_json(json_path):
    """JSON에서 라인별 (start_s, end_s, text) 리스트 반환"""
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    words = data['aligned_words']
    lines = []  # [(start_s, end_s, text, is_section_end)]
    current_line = ""
    current_start = None
    current_end = None

    for w in words:
        word = w['word']
        start = w['start_s']
        end = w['end_s']

        word = re.sub(r'\[.*?\]', '', word, flags=re.DOTALL)

        if current_start is None:
            current_start = start

        if word.endswith('\n'):
            current_line += word.rstrip('\n')
            current_end = end
            text = current_line.strip()
            # \n\n = 섹션 경계 (Verse/Chorus 등)
            is_section_end = word.endswith('\n\n')
            if text and not re.match(r'^\[.*\]$', text, re.DOTALL):
                lines.append((current_start, current_end, text, is_section_end))
            current_line = ""
            current_start = None
            current_end = None
        else:
            current_line += word
            current_end = end

    if current_line.strip() and current_start is not None:
        lines.append((current_start, current_end, current_line.strip(), False))

    def decap_for_merge(text):
        """합칠 때 줄 시작 대문자를 소문자로 (I, 전체 대문자 단어 제외)"""
        if not text:
            return text
        first_word = text.split()[0] if text.split() else ""
        if first_word == "I" or first_word.isupper():
            return text
        return text[0].lower() + text[1:]

    # 따옴표가 열린 채 줄이 끊기면 닫힐 때까지 합치기
    quote_merged = []
    i = 0
    while i < len(lines):
        start_s, end_s, text, is_section_end = lines[i]
        open_quotes = text.count('"') + text.count('\u201c')
        close_quotes = text.count('"') + text.count('\u201d')
        # 홑따옴표는 apostrophe와 구분이 어려우므로 쌍따옴표만 처리
        if open_quotes > close_quotes:
            # 따옴표가 닫힐 때까지 다음 줄 합치기
            while i + 1 < len(lines):
                i += 1
                _, next_end, next_text, is_section_end = lines[i]
                text = f"{text} {decap_for_merge(next_text)}"
                end_s = next_end
                close_quotes += next_text.count('"') + next_text.count('\u201d')
                if close_quotes >= open_quotes:
                    break
        quote_merged.append((start_s, end_s, text, is_section_end))
        i += 1

    # 짧은 줄 합치기 (3단어 이하면 다음 줄과 병합, 섹션 경계는 넘지 않음)
    merged = []
    i = 0
    while i < len(quote_merged):
        start_s, end_s, text, is_section_end = quote_merged[i]
        word_count = len(text.split())
        if word_count <= 3 and i + 1 < len(quote_merged) and not is_section_end:
            next_start, next_end, next_text, next_section_end = quote_merged[i + 1]
            next_word_count = len(next_text.split())
            if next_word_count <= 3 and word_count + next_word_count <= 8:
                merged.append((start_s, next_end, f"{text} {decap_for_merge(next_text)}"))
                i += 2
                continue
        merged.append((start_s, end_s, text))
        i += 1

    # end_s 여유 추가 (다음 줄 시작을 넘지 않도록)
    LYRICS_EXTEND = 0.8
    for i in range(len(merged)):
        start_s, end_s, text = merged[i]
        extended_end = end_s + LYRICS_EXTEND
        if i + 1 < len(merged):
            next_start = merged[i + 1][0]
            extended_end = min(extended_end, next_start)
        merged[i] = (start_s, extended_end, text)

    return merged


# ============================================================
# 단독 실행
# ============================================================
if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python3 lyrics.py <EP 폴더 경로>")
        sys.exit(1)

    ep_dir = os.path.abspath(sys.argv[1])
    songs_dir = os.path.join(ep_dir, "songs")
    lyrics_dir = os.path.join(ep_dir, "lyrics")

    song_files = sorted([
        f for f in os.listdir(songs_dir)
        if os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
    ])

    if not song_files:
        print(f"❌ songs 폴더에 오디오 파일이 없습니다: {songs_dir}")
        sys.exit(1)

    print(f"📂 EP: {os.path.basename(ep_dir)}")
    print(f"📋 {len(song_files)}곡 발견\n")
    sync_lyrics(songs_dir, lyrics_dir, song_files)
