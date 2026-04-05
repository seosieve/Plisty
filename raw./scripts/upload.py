#!/usr/bin/env python3
"""
raw. YouTube 업로드 스크립트
사용법: python3 upload.py <EP 폴더 경로>
예: python3 upload.py ../EP03_260406

- outputs/ 폴더의 mp4 파일 업로드
- 타임라인 txt를 설명란에 자동 삽입
- loops/ 폴더의 png를 썸네일로 설정
"""

import os
import sys
import re
import json
import pickle
import subprocess

from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

TEAM_KR = {
    "SSG LANDERS": "SSG 랜더스",
    "HANWHA EAGLES": "한화 이글스",
    "KIA TIGERS": "기아 타이거즈",
    "LG TWINS": "LG 트윈스",
    "DOOSAN BEARS": "두산 베어스",
    "KT WIZ": "KT 위즈",
    "NC DINOS": "NC 다이노스",
    "SAMSUNG LIONS": "삼성 라이온즈",
    "LOTTE GIANTS": "롯데 자이언츠",
    "KIWOOM HEROES": "키움 히어로즈",
}

BASE_TAGS = [
    "raw", "KBO", "lofi", "lofi jazz", "playlist",
    "야구", "응원가", "공부음악", "집중음악", "카페음악", "bgm",
]

# 팀별 태그: [약칭, 풀네임, 팀명+플레이리스트]
TEAM_TAGS = {
    "SSG LANDERS": ["SSG", "SSG랜더스", "랜더스 플레이리스트"],
    "HANWHA EAGLES": ["한화", "한화이글스", "이글스 플레이리스트"],
    "KIA TIGERS": ["기아", "기아타이거즈", "타이거즈 플레이리스트"],
    "LG TWINS": ["LG", "LG트윈스", "트윈스 플레이리스트"],
    "DOOSAN BEARS": ["두산", "두산베어스", "베어스 플레이리스트"],
    "KT WIZ": ["KT", "KT위즈", "위즈 플레이리스트"],
    "NC DINOS": ["NC", "NC다이노스", "다이노스 플레이리스트"],
    "SAMSUNG LIONS": ["삼성", "삼성라이온즈", "라이온즈 플레이리스트"],
    "LOTTE GIANTS": ["롯데", "롯데자이언츠", "자이언츠 플레이리스트"],
    "KIWOOM HEROES": ["키움", "키움히어로즈", "히어로즈 플레이리스트"],
}

TEAM_EMOJI = {
    "SSG LANDERS": "🐕",
    "HANWHA EAGLES": "🦅",
    "KIA TIGERS": "🐯",
    "LG TWINS": "👬",
    "DOOSAN BEARS": "🐻",
    "KT WIZ": "🧙",
    "NC DINOS": "🦕",
    "SAMSUNG LIONS": "🦁",
    "LOTTE GIANTS": "🪶",
    "KIWOOM HEROES": "🦸",
}

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CLIENT_SECRET = os.path.join(SCRIPT_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(SCRIPT_DIR, "token.pickle")

CHANNEL_NAME = "raw."


def get_authenticated_service():
    """OAuth2 인증 → YouTube API 서비스 반환"""
    creds = None
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET):
                print(f"❌ OAuth 클라이언트 시크릿 파일이 없습니다:")
                print(f"   {CLIENT_SECRET}")
                print()
                print("📋 설정 방법:")
                print("   1. Google Cloud Console → API 및 서비스 → 사용자 인증 정보")
                print("   2. OAuth 2.0 클라이언트 ID 생성 (데스크톱 앱)")
                print("   3. JSON 다운로드 → 위 경로에 저장")
                print("   4. YouTube Data API v3 활성화")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)
        print("✅ 인증 완료")

    return build("youtube", "v3", credentials=creds)


def generate_theme(team_kr):
    """Claude Code CLI로 제목 테마 문구 생성 (이모지 포함)"""
    prompt = f"""KBO '{team_kr}' 응원가 lofi 플레이리스트 영상 제목 문구를 만들어줘.

이 플레이리스트는 공부, 작업, 출근, 카페 등 일상에서 틀어놓는 BGM이야.
야구 팬이지만 일상도 살아야 하는 사람들의 느낌을 담아줘.

톤:
- 못 가서 슬프다가 아니라, 가슴속에 항상 품고 있는 느낌
- 일상 속에서도 야구를 함께하는 따뜻한 감성
- 언젠간 갈 거야, 항상 함께야 같은 긍정적인 톤
- 공부, 작업, 출근, 카페 등 일상 상황과 야구를 자연스럽게 엮기

규칙:
1. 반드시 20자 이내 (공백 포함)
2. 이모지는 문장 끝에 정확히 1개 (이모지는 글자 수에 미포함)
3. 이모지 앞에 띄어쓰기 1개
4. 팀명이나 연고지, 일상 상황을 자연스럽게 활용
5. lofi, 음악, 플레이리스트 등 음악 관련 단어 사용 금지
6. 위트있고 공감가게

정확히 1개의 문구만 출력해. 여러 개 나열하지 마. 설명이나 따옴표 없이."""

    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, timeout=120
    )
    return result.stdout.strip()


def extract_emoji(theme):
    """테마 문구 끝의 이모지 추출"""
    import emoji
    chars = list(theme.rstrip())
    for i in range(len(chars) - 1, -1, -1):
        if emoji.is_emoji(chars[i]):
            return chars[i]
    return ""


def generate_intro(team_kr, theme_emoji):
    """Claude Code CLI로 설명란 인트로 문구 생성"""
    prompt = f"""KBO '{team_kr}' 응원가 플레이리스트 영상 설명란 인트로 문구를 만들어줘.

예시:
2026 시즌, 한화는 참새가 아닙니다.
올해는 진짜 독수리입니다.

규칙:
1. 정확히 2줄, 짧고 임팩트 있게
2. 팀 별명/마스코트/연고지 등 팀 특징 활용
3. 팬이 공감할 수 있는 응원 느낌
4. lofi, 음악, 플레이리스트 등 음악 관련 단어 사용 금지
5. 이모지 사용 금지
6. 마지막 줄 뒤에 빈 줄 넣지 마

문구만 출력해. 설명이나 따옴표 없이."""

    result = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, timeout=120
    )
    intro = result.stdout.strip()
    return f"{intro}\n\n이건 그런 당신을 위한 플레이리스트입니다. {theme_emoji}"


def get_duration(filepath):
    """ffprobe로 오디오 길이 반환"""
    result = subprocess.run(
        ['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', filepath],
        capture_output=True, text=True
    )
    return float(result.stdout.strip())


def build_timeline(songs_dir):
    """songs/ 폴더에서 곡 순서대로 타임라인 생성"""
    audio_ext = {'.wav', '.mp3', '.flac', '.m4a', '.aac'}
    files = sorted(f for f in os.listdir(songs_dir) if os.path.splitext(f)[1].lower() in audio_ext)

    lines = []
    total = 0.0
    for f in files:
        t = round(total)
        m, s = int(t // 60), int(t % 60)
        title = re.sub(r'^\d+_', '', os.path.splitext(f)[0])
        lines.append(f"{m:02d}:{s:02d} {title}")
        total += get_duration(os.path.join(songs_dir, f))
    # 2회차 시작 시점에 REPEAT 표시
    t = round(total)
    m, s = int(t // 60), int(t % 60)
    lines.append(f"{m:02d}:{s:02d} REPEAT")
    return lines


def build_description(intro, songs_dir, team_kr, ep_name):
    """인트로 + 타임라인 + 저작권 고지 + 해시태그로 설명란 생성"""
    # 타임라인
    timeline_lines = build_timeline(songs_dir)

    # 팀명에서 공백 제거한 해시태그
    team_tag = team_kr.replace(" ", "")

    desc = intro
    desc += "\n\n—\n\n"
    desc += "\n".join(timeline_lines)
    desc += "\n\n—\n\n"
    desc += "*본 영상의 광고 수익은 원저작권자에게 쉐어됩니다.*\n"
    desc += "*야구장에서 들었던 노래들을 추억하며,*\n"
    desc += "*AI를 활용해 개인이 제작 및 편집한 팬 메이드 커버 음원 및 영상입니다.*\n"
    desc += "*원작자의 저작권을 존중하며,*\n"
    desc += "*문제가 있을 시 메일로 연락 주시면 즉시 조치하겠습니다.*\n"
    desc += "\n"
    desc += "All ad revenue from this video goes to the original copyright holder.\n"
    desc += "This is a fan-made cover playlist inspired by the songs we heard at the ballpark,\n"
    desc += "produced and edited using AI.\n"
    desc += "We respect the rights of the original creators.\n"
    desc += "If there are any issues, please contact me via email,\n"
    desc += "and I will take immediate action upon review.\n"
    desc += "\n"
    desc += "📩 rawdot.music@gmail.com\n"
    desc += "\n—\n\n"
    # 날짜에서 월 추출
    month_match = re.search(r'_\d{2}(\d{2})\d{2}$', ep_name) if ep_name else None
    month_tag = f"#{int(month_match.group(1))}월" if month_match else ""

    desc += f"#KBO #{team_tag} #응원가 #playlist #lofi #공부음악 #집중음악 #공부 #백색소음 #bgm #브금 #야구 #2026 #중간고사 #기말고사 #시험기간 #카페음악 #카페bgm #카페재즈 #카페플레이리스트 {month_tag} #{team_tag}플레이리스트"

    return desc


def main():
    if len(sys.argv) < 2:
        print("사용법: python3 upload.py <EP 폴더 경로>")
        print("예: python3 upload.py ../EP03_260406")
        sys.exit(1)

    ep_dir = os.path.abspath(sys.argv[1])
    ep_name = os.path.basename(ep_dir)
    output_dir = os.path.join(ep_dir, "outputs")
    loops_dir = os.path.join(ep_dir, "loops")

    # EP 번호 추출
    ep_match = re.search(r'EP(\d+)', ep_name)
    if not ep_match:
        print(f"❌ EP 번호를 찾을 수 없습니다: {ep_name}")
        sys.exit(1)
    ep_num = int(ep_match.group(1))

    # mp4 파일 찾기
    mp4_files = [f for f in os.listdir(output_dir) if f.endswith(".mp4")]
    if not mp4_files:
        print(f"❌ outputs 폴더에 mp4 파일이 없습니다: {output_dir}")
        sys.exit(1)
    video_path = os.path.join(output_dir, mp4_files[0])

    songs_dir = os.path.join(ep_dir, "songs")

    # 팀명 추출 (루프 파일명)
    loop_files = [f for f in os.listdir(loops_dir) if f.endswith((".png", ".jpg", ".jpeg"))]
    loop_name = os.path.splitext(loop_files[0])[0] if loop_files else ""
    team_kr = TEAM_KR.get(loop_name.upper(), loop_name)

    # 썸네일
    thumbnail_path = os.path.join(loops_dir, loop_files[0]) if loop_files else None

    # 예약 시간 (폴더명에서 날짜 추출, 아침 7시 KST)
    date_match = re.search(r'_(\d{6})$', ep_name)
    if date_match:
        d = date_match.group(1)
        publish_at = f"20{d[:2]}-{d[2:4]}-{d[4:6]}T07:00:00+09:00"
    else:
        publish_at = None

    # 썸네일 검증 (4K PNG)
    if not thumbnail_path:
        print("❌ 썸네일 파일이 없습니다 (loops/ 폴더에 png 파일 필요)")
        sys.exit(1)
    if not thumbnail_path.lower().endswith(".png"):
        print(f"❌ 썸네일이 PNG가 아닙니다: {os.path.basename(thumbnail_path)}")
        sys.exit(1)
    from PIL import Image as PILImage
    thumb_img = PILImage.open(thumbnail_path)
    tw, th = thumb_img.size
    thumb_img.close()
    if tw != 3840 or th != 2160:
        print(f"❌ 썸네일이 4K(3840x2160)가 아닙니다: {tw}x{th}")
        sys.exit(1)

    # AI 문구 생성
    mascot_emoji = TEAM_EMOJI.get(loop_name.upper(), "⚾")

    print("🤖 제목 문구 생성 중...")
    theme = generate_theme(team_kr)
    title = f"𝑷𝑳𝑨𝒀𝑳𝑰𝑺𝑻 | {theme} | Lo-fi Jazz {team_kr} 응원가 플리"

    print("🤖 인트로 문구 생성 중...")
    intro = generate_intro(team_kr, mascot_emoji)

    # 설명
    description = build_description(intro, songs_dir, team_kr, ep_name)

    print(f"📹 영상: {os.path.basename(video_path)}")
    print(f"📝 제목: {title}")
    print(f"🖼️  썸네일: {os.path.basename(thumbnail_path) if thumbnail_path else '없음'}")
    print(f"📅 예약: {publish_at if publish_at else '없음 (비공개)'}")
    print(f"📋 설명:")
    for line in description.split("\n"):
        print(f"   {line}")
    print()


    # 인증
    print("\n🔐 YouTube 인증 중...")
    youtube = get_authenticated_service()

    # 업로드
    print("📤 업로드 중...")
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": BASE_TAGS + TEAM_TAGS.get(loop_name.upper(), []),
            "categoryId": "10",  # Music
        },
        "status": {
            "privacyStatus": "private",
            "selfDeclaredMadeForKids": False,
            **({"publishAt": publish_at} if publish_at else {}),
        },
    }

    media = MediaFileUpload(video_path, chunksize=10 * 1024 * 1024, resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            print(f"  ⏳ {pct}%")

    video_id = response["id"]
    print(f"✅ 업로드 완료! ID: {video_id}")

    # 썸네일 설정
    if thumbnail_path and os.path.exists(thumbnail_path):
        print("🖼️  썸네일 설정 중...")
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(thumbnail_path)
        ).execute()
        print("✅ 썸네일 설정 완료")

    # 재생목록에 추가
    print("📋 재생목록 추가 중...")
    playlists = youtube.playlists().list(part="snippet", mine=True, maxResults=50).execute()
    playlist_id = None
    for pl in playlists.get("items", []):
        if "KBO 2026" in pl["snippet"]["title"]:
            playlist_id = pl["id"]
            break

    if playlist_id:
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {"kind": "youtube#video", "videoId": video_id},
                }
            }
        ).execute()
        print("✅ 재생목록 추가 완료")
    else:
        print("⚠️ 'KBO 2026' 재생목록을 찾을 수 없습니다")

    url = f"https://youtu.be/{video_id}"
    print(f"\n🎉 완료!{' (예약: ' + publish_at + ')' if publish_at else ' (비공개)'}")
    print(f"🔗 {url}")


if __name__ == "__main__":
    main()
