#!/bin/bash
# ============================================================
# SEOUL LABS Playlist Video Generator
# 음악 파일 + 배경 이미지 → 텍스트 오버레이 포함 영상 자동 생성
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SONGS_DIR="$SCRIPT_DIR/songs"
IMAGES_DIR="$SCRIPT_DIR/images"
OUTPUT_DIR="$SCRIPT_DIR/output"
TRACKLIST="$SCRIPT_DIR/tracklist.json"
TEMP_DIR="$SCRIPT_DIR/.tmp_playlist"

# 배경 이미지 (변경 가능)
BG_IMAGE="$IMAGES_DIR/SnowFlake_Extend.png"

# 출력 설정
OUTPUT_FILE="$OUTPUT_DIR/playlist_output.mp4"
RESOLUTION="1920:1080"

# 폰트 설정
FONT_PATH="$HOME/Library/Fonts/Roboto-LightItalic.ttf"

# 텍스트 오버레이 설정
TEXT_FADE_IN=1        # 페이드 인 지속 시간 (초)
TEXT_DISPLAY=5        # 텍스트 표시 시간 (초)
TEXT_FADE_OUT=1       # 페이드 아웃 지속 시간 (초)
TEXT_FONT_SIZE=38
TEXT_COLOR="0xEEEEEE"
TEXT_X="(w-text_w)/2"  # 수평 중앙
TEXT_Y="h-120"        # 하단 여백

# ============================================================
# 사전 확인
# ============================================================
echo "🎬 SEOUL LABS Playlist Video Generator"
echo "========================================"

if [ ! -f "$TRACKLIST" ]; then
    echo "❌ tracklist.json 파일을 찾을 수 없습니다."
    exit 1
fi

if [ ! -f "$BG_IMAGE" ]; then
    echo "❌ 배경 이미지를 찾을 수 없습니다: $BG_IMAGE"
    exit 1
fi

TRACK_COUNT=$(jq length "$TRACKLIST")
echo "📋 트랙 수: $TRACK_COUNT"

# 모든 음악 파일 존재 확인
for i in $(seq 0 $((TRACK_COUNT - 1))); do
    FILE=$(jq -r ".[$i].file" "$TRACKLIST")
    if [ ! -f "$SONGS_DIR/$FILE" ]; then
        echo "❌ 음악 파일을 찾을 수 없습니다: $FILE"
        exit 1
    fi
done

echo "✅ 모든 파일 확인 완료"

# ============================================================
# Step 1: 각 곡 duration 계산 & 시작 시점 기록
# ============================================================
echo ""
echo "📊 트랙 정보 분석 중..."

declare -a STARTS
declare -a DURATIONS
declare -a TITLES
declare -a ARTISTS
CURRENT_TIME=0

for i in $(seq 0 $((TRACK_COUNT - 1))); do
    FILE=$(jq -r ".[$i].file" "$TRACKLIST")
    TITLE=$(jq -r ".[$i].title" "$TRACKLIST")
    ARTIST=$(jq -r ".[$i].artist" "$TRACKLIST")

    DURATION=$(ffprobe -v quiet -show_entries format=duration -of csv=p=0 "$SONGS_DIR/$FILE")

    STARTS[$i]=$CURRENT_TIME
    DURATIONS[$i]=$DURATION
    TITLES[$i]=$TITLE
    ARTISTS[$i]=$ARTIST

    # 시간 포맷팅 (MM:SS)
    MINS=$(echo "$CURRENT_TIME" | awk '{printf "%d", $1/60}')
    SECS=$(echo "$CURRENT_TIME" | awk '{printf "%02d", int($1)%60}')
    DUR_MINS=$(echo "$DURATION" | awk '{printf "%d", $1/60}')
    DUR_SECS=$(echo "$DURATION" | awk '{printf "%02d", int($1)%60}')

    echo "  [$((i+1))] $TITLE - $ARTIST  (${DUR_MINS}:${DUR_SECS})  @ ${MINS}:${SECS}"

    CURRENT_TIME=$(echo "$CURRENT_TIME + $DURATION" | bc)
done

TOTAL_MINS=$(echo "$CURRENT_TIME" | awk '{printf "%d", $1/60}')
TOTAL_SECS=$(echo "$CURRENT_TIME" | awk '{printf "%02d", int($1)%60}')
echo ""
echo "⏱  총 재생시간: ${TOTAL_MINS}:${TOTAL_SECS}"

# ============================================================
# Step 2: 오디오 파일 합치기 (개별 디코딩 → filter_complex)
# ============================================================
echo ""
echo "🎵 오디오 합치는 중..."

mkdir -p "$TEMP_DIR"
mkdir -p "$OUTPUT_DIR"

# 각 MP3를 개별적으로 WAV 디코딩 (앨범아트 스트림 제거, 통일된 포맷)
INPUTS=""
FILTER=""
for i in $(seq 0 $((TRACK_COUNT - 1))); do
    FILE=$(jq -r ".[$i].file" "$TRACKLIST")
    WAV_FILE="$TEMP_DIR/track_${i}.wav"
    ffmpeg -y -i "$SONGS_DIR/$FILE" -map 0:a:0 -ar 48000 -ac 2 -c:a pcm_s16le "$WAV_FILE" 2>/dev/null
    INPUTS="$INPUTS -i $WAV_FILE"
    FILTER="${FILTER}[$i:a:0]"
done

FILTER="${FILTER}concat=n=${TRACK_COUNT}:v=0:a=1[outa]"

MERGED_AUDIO="$TEMP_DIR/merged_audio.wav"
eval ffmpeg -y $INPUTS -filter_complex \"$FILTER\" -map \"[outa]\" \"$MERGED_AUDIO\" 2>/dev/null

echo "✅ 오디오 합치기 완료"

# ============================================================
# Step 3: drawtext 필터 생성 (텍스트 오버레이)
# ============================================================
echo ""
echo "✍️  텍스트 오버레이 준비 중..."

DRAWTEXT_FILTERS=""

for i in $(seq 0 $((TRACK_COUNT - 1))); do
    START=${STARTS[$i]}
    TITLE="${TITLES[$i]}"
    ARTIST="${ARTISTS[$i]}"
    DISPLAY_TEXT="${TITLE}"

    # 한글/특수문자 안전을 위해 텍스트를 파일로 저장
    TEXT_FILE="$TEMP_DIR/text_${i}.txt"
    echo -n "$DISPLAY_TEXT" > "$TEXT_FILE"

    # 타이밍 계산: 곡 시작 시 페이드 인, 곡 끝나기 1초 전 페이드 아웃
    SONG_DURATION=${DURATIONS[$i]}
    FADE_IN_START=$(echo "$START" | bc)
    FADE_IN_END=$(echo "$START + $TEXT_FADE_IN" | bc)
    FADE_OUT_START=$(echo "$START + $SONG_DURATION - 1 - $TEXT_FADE_OUT" | bc)
    FADE_OUT_END=$(echo "$START + $SONG_DURATION - 1" | bc)

    # drawtext 필터 (알파 채널로 페이드 인/아웃)
    ALPHA="if(lt(t\\,$FADE_IN_START)\\,0\\, if(lt(t\\,$FADE_IN_END)\\,(t-$FADE_IN_START)/$TEXT_FADE_IN\\, if(lt(t\\,$FADE_OUT_START)\\,1\\, if(lt(t\\,$FADE_OUT_END)\\,1-(t-$FADE_OUT_START)/$TEXT_FADE_OUT\\, 0))))"

    FILTER="drawtext=fontfile='$FONT_PATH':textfile='$TEXT_FILE':fontsize=$TEXT_FONT_SIZE:fontcolor=${TEXT_COLOR}:borderw=1:bordercolor=0x373737:x=$TEXT_X:y=$TEXT_Y:alpha='$ALPHA'"

    if [ -z "$DRAWTEXT_FILTERS" ]; then
        DRAWTEXT_FILTERS="$FILTER"
    else
        DRAWTEXT_FILTERS="$DRAWTEXT_FILTERS,$FILTER"
    fi
done

# ============================================================
# Step 4: 최종 영상 생성
# ============================================================
echo ""
echo "🎬 영상 생성 중... (시간이 좀 걸립니다)"

TOTAL_DURATION=$(echo "$CURRENT_TIME" | bc)

ffmpeg -y \
    -loop 1 -i "$BG_IMAGE" \
    -i "$MERGED_AUDIO" \
    -vf "scale=${RESOLUTION}:force_original_aspect_ratio=decrease,pad=${RESOLUTION}:(ow-iw)/2:(oh-ih)/2:color=black,${DRAWTEXT_FILTERS}" \
    -c:v libx264 -preset medium -crf 18 -tune stillimage \
    -c:a aac -b:a 192k \
    -shortest \
    -pix_fmt yuv420p \
    "$OUTPUT_FILE" 2>"$TEMP_DIR/ffmpeg_final.log"

if [ ! -f "$OUTPUT_FILE" ]; then
    echo "❌ 영상 생성 실패. 로그:"
    cat "$TEMP_DIR/ffmpeg_final.log"
    rm -rf "$TEMP_DIR"
    exit 1
fi

# 임시 파일 정리
rm -rf "$TEMP_DIR"

echo ""
echo "========================================"
echo "✅ 영상 생성 완료!"
echo "📁 출력: $OUTPUT_FILE"

# 출력 파일 정보
OUTPUT_SIZE=$(ls -lh "$OUTPUT_FILE" | awk '{print $5}')
echo "📦 파일 크기: $OUTPUT_SIZE"
echo "⏱  총 재생시간: ${TOTAL_MINS}:${TOTAL_SECS}"
echo "========================================"
