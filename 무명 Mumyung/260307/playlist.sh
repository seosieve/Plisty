#!/bin/bash
# ============================================================
# Mumyung Playlist Video Generator
# 음악 파일 + 배경 이미지 → 텍스트 오버레이 포함 영상 자동 생성
# 이미지 모드: 1장(고정), 곡 수와 동일(곡별 전환), 그 외(균등 분할)
# ============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SONGS_DIR="$SCRIPT_DIR/songs"
IMAGES_DIR="$SCRIPT_DIR/images"
OUTPUT_DIR="$SCRIPT_DIR/output"
TRACKLIST="$SCRIPT_DIR/tracklist.json"
TEMP_DIR="$SCRIPT_DIR/.tmp_playlist"

# 출력 설정
OUTPUT_FILE="$OUTPUT_DIR/playlist_output.mp4"
RESOLUTION="1920:1080"
RES_W=1920
RES_H=1080

# 폰트 설정
FONT_PATH="$HOME/Library/Fonts/MapoFlowerIsland.otf"

# 텍스트 오버레이 설정
TEXT_FADE_IN=1
TEXT_DISPLAY=5
TEXT_FADE_OUT=1
TEXT_FONT_SIZE=38
TEXT_COLOR="0xEEEEEE"
TEXT_X="(w-text_w)/2"
TEXT_Y="h-120"

# ============================================================
# 사전 확인
# ============================================================
echo "🎬 Mumyung Playlist Video Generator"
echo "========================================"

if [ ! -f "$TRACKLIST" ]; then
    echo "❌ tracklist.json 파일을 찾을 수 없습니다."
    exit 1
fi

TRACK_COUNT=$(jq length "$TRACKLIST")
echo "📋 트랙 수: $TRACK_COUNT"

# 곡 제목과 동일한 이름의 이미지 매칭
IMAGE_FILES=()
EXTS=("png" "jpg" "jpeg" "webp")

for i in $(seq 0 $((TRACK_COUNT - 1))); do
    TITLE=$(jq -r ".[$i].title" "$TRACKLIST")
    FILE=$(jq -r ".[$i].file" "$TRACKLIST")
    FOUND_IMG=""

    # 음악 파일 존재 확인
    if [ ! -f "$SONGS_DIR/$FILE" ]; then
        echo "❌ 음악 파일을 찾을 수 없습니다: $FILE"
        exit 1
    fi

    # 곡 제목과 동일한 이미지 탐색
    for ext in "${EXTS[@]}"; do
        if [ -f "$IMAGES_DIR/$TITLE.$ext" ]; then
            FOUND_IMG="$IMAGES_DIR/$TITLE.$ext"
            break
        fi
    done

    if [ -n "$FOUND_IMG" ]; then
        IMAGE_FILES+=("$FOUND_IMG")
    else
        echo "⚠️  이미지 없음: $TITLE (images/ 폴더에 '$TITLE.jpg' 등 필요)"
    fi
done

IMAGE_COUNT=${#IMAGE_FILES[@]}

if [ "$IMAGE_COUNT" -eq 0 ]; then
    echo "❌ 매칭되는 이미지가 하나도 없습니다."
    exit 1
fi

echo "🖼  이미지 매칭: ${IMAGE_COUNT}/${TRACK_COUNT}곡"
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

    MINS=$(echo "$CURRENT_TIME" | awk '{printf "%d", $1/60}')
    SECS=$(echo "$CURRENT_TIME" | awk '{printf "%02d", int($1)%60}')
    DUR_MINS=$(echo "$DURATION" | awk '{printf "%d", $1/60}')
    DUR_SECS=$(echo "$DURATION" | awk '{printf "%02d", int($1)%60}')

    echo "  [$((i+1))] $TITLE - $ARTIST  (${DUR_MINS}:${DUR_SECS})  @ ${MINS}:${SECS}"

    CURRENT_TIME=$(echo "$CURRENT_TIME + $DURATION" | bc)
done

TOTAL_DURATION=$(echo "$CURRENT_TIME" | bc)
TOTAL_MINS=$(echo "$CURRENT_TIME" | awk '{printf "%d", $1/60}')
TOTAL_SECS=$(echo "$CURRENT_TIME" | awk '{printf "%02d", int($1)%60}')
echo ""
echo "⏱  총 재생시간: ${TOTAL_MINS}:${TOTAL_SECS}"

# ============================================================
# Step 2: 오디오 파일 합치기
# ============================================================
echo ""
echo "🎵 오디오 합치는 중..."

mkdir -p "$TEMP_DIR"
mkdir -p "$OUTPUT_DIR"

CONCAT_LIST="$TEMP_DIR/audio_list.txt"
> "$CONCAT_LIST"
for i in $(seq 0 $((TRACK_COUNT - 1))); do
    FILE=$(jq -r ".[$i].file" "$TRACKLIST")
    WAV_FILE="$TEMP_DIR/track_${i}.wav"
    ffmpeg -y -i "$SONGS_DIR/$FILE" -ar 48000 -ac 2 -c:a pcm_s16le "$WAV_FILE" 2>/dev/null
    echo "file '$WAV_FILE'" >> "$CONCAT_LIST"
done

MERGED_AUDIO="$TEMP_DIR/merged_audio.wav"
ffmpeg -y -f concat -safe 0 -i "$CONCAT_LIST" -c copy "$MERGED_AUDIO" 2>/dev/null

echo "✅ 오디오 합치기 완료"

# ============================================================
# Step 3: 이미지별 비디오 세그먼트 생성
# ============================================================
echo ""
echo "🖼  이미지 세그먼트 생성 중..."

# 각 이미지의 표시 시간 계산
declare -a IMG_STARTS
declare -a IMG_DURATIONS

if [ "$IMAGE_COUNT" -eq 1 ]; then
    IMG_STARTS[0]=0
    IMG_DURATIONS[0]=$TOTAL_DURATION
elif [ "$IMAGE_COUNT" -eq "$TRACK_COUNT" ]; then
    for i in $(seq 0 $((TRACK_COUNT - 1))); do
        IMG_STARTS[$i]=${STARTS[$i]}
        IMG_DURATIONS[$i]=${DURATIONS[$i]}
    done
else
    SEGMENT_DUR=$(echo "$TOTAL_DURATION / $IMAGE_COUNT" | bc -l)
    for i in $(seq 0 $((IMAGE_COUNT - 1))); do
        IMG_STARTS[$i]=$(echo "$SEGMENT_DUR * $i" | bc -l)
        IMG_DURATIONS[$i]=$SEGMENT_DUR
    done
fi

# 각 이미지를 비디오 세그먼트로 변환
XFADE_DUR=2  # cross dissolve 전환 시간 (초)

for i in $(seq 0 $((IMAGE_COUNT - 1))); do
    SEG_DUR=${IMG_DURATIONS[$i]}
    SEG_FILE="$TEMP_DIR/seg_${i}.mp4"
    ffmpeg -y -loop 1 -i "${IMAGE_FILES[$i]}" \
        -t "$SEG_DUR" \
        -vf "scale=${RESOLUTION}:force_original_aspect_ratio=increase,crop=${RESOLUTION}" \
        -c:v libx264 -preset medium -crf 18 -tune stillimage \
        -pix_fmt yuv420p -r 25 \
        "$SEG_FILE" 2>/dev/null
done

# xfade로 cross dissolve 적용하여 합치기
MERGED_VIDEO="$TEMP_DIR/merged_video.mp4"

if [ "$IMAGE_COUNT" -eq 1 ]; then
    cp "$TEMP_DIR/seg_0.mp4" "$MERGED_VIDEO"
else
    # xfade 필터 체인 구성
    XFADE_INPUTS=""
    XFADE_FILTER=""
    OFFSET=0

    for i in $(seq 0 $((IMAGE_COUNT - 1))); do
        XFADE_INPUTS="$XFADE_INPUTS -i $TEMP_DIR/seg_${i}.mp4"
    done

    # xfade 필터 체인: [0:v][1:v]xfade...[xf1]; [xf1][2:v]xfade...[xf2]; ...
    OFFSET=$(echo "${IMG_DURATIONS[0]} - $XFADE_DUR" | bc -l)
    XFADE_FILTER="[0:v][1:v]xfade=transition=fade:duration=${XFADE_DUR}:offset=${OFFSET}[xf1]"

    for i in $(seq 2 $((IMAGE_COUNT - 1))); do
        PREV="xf$((i-1))"
        NEXT="xf${i}"
        OFFSET=$(echo "$OFFSET + ${IMG_DURATIONS[$((i-1))]} - $XFADE_DUR" | bc -l)
        XFADE_FILTER="${XFADE_FILTER};[${PREV}][${i}:v]xfade=transition=fade:duration=${XFADE_DUR}:offset=${OFFSET}[${NEXT}]"
    done

    LAST_LABEL="xf$((IMAGE_COUNT-1))"

    # ffmpeg 명령을 스크립트로 생성하여 실행 (경로 이스케이프 문제 회피)
    XFADE_CMD="$TEMP_DIR/xfade_cmd.sh"
    {
        echo "#!/bin/bash"
        echo -n "ffmpeg -y"
        for j in $(seq 0 $((IMAGE_COUNT - 1))); do
            echo -n " -i '$TEMP_DIR/seg_${j}.mp4'"
        done
        echo " -filter_complex '${XFADE_FILTER}' -map '[${LAST_LABEL}]' -c:v libx264 -preset medium -crf 18 -pix_fmt yuv420p '$MERGED_VIDEO' 2>/dev/null"
    } > "$XFADE_CMD"
    chmod +x "$XFADE_CMD"
    bash "$XFADE_CMD"
fi

echo "✅ 이미지 세그먼트 생성 완료"

# ============================================================
# Step 4: drawtext 필터 생성 (텍스트 오버레이)
# ============================================================
echo ""
echo "✍️  텍스트 오버레이 준비 중..."

DRAWTEXT_FILTERS=""

for i in $(seq 0 $((TRACK_COUNT - 1))); do
    START=${STARTS[$i]}
    TITLE="${TITLES[$i]}"

    # 번호 + 제목 (01. 제목 형식)
    TRACK_NUM=$(printf "%02d" $((i+1)))
    DISPLAY_TEXT="${TRACK_NUM}. ${TITLE}"

    # 한글 텍스트를 파일로 저장 (drawtext 이스케이프 문제 회피)
    TEXT_FILE="$TEMP_DIR/text_${i}.txt"
    echo -n "$DISPLAY_TEXT" > "$TEXT_FILE"

    SONG_DURATION=${DURATIONS[$i]}
    FADE_IN_START=$(echo "$START" | bc)
    FADE_IN_END=$(echo "$START + $TEXT_FADE_IN" | bc)
    FADE_OUT_START=$(echo "$START + $SONG_DURATION - 1 - $TEXT_FADE_OUT" | bc)
    FADE_OUT_END=$(echo "$START + $SONG_DURATION - 1" | bc)

    ALPHA="if(lt(t\\,$FADE_IN_START)\\,0\\, if(lt(t\\,$FADE_IN_END)\\,(t-$FADE_IN_START)/$TEXT_FADE_IN\\, if(lt(t\\,$FADE_OUT_START)\\,1\\, if(lt(t\\,$FADE_OUT_END)\\,1-(t-$FADE_OUT_START)/$TEXT_FADE_OUT\\, 0))))"

    FILTER="drawtext=fontfile='$FONT_PATH':textfile='$TEXT_FILE':fontsize=$TEXT_FONT_SIZE:fontcolor=${TEXT_COLOR}:borderw=1:bordercolor=0x373737:x=$TEXT_X:y=$TEXT_Y:alpha='$ALPHA'"

    if [ -z "$DRAWTEXT_FILTERS" ]; then
        DRAWTEXT_FILTERS="$FILTER"
    else
        DRAWTEXT_FILTERS="$DRAWTEXT_FILTERS,$FILTER"
    fi
done

# ============================================================
# Step 5: 최종 영상 생성 (비디오 + 오디오 + 텍스트)
# ============================================================
echo ""
echo "🎬 영상 생성 중... (시간이 좀 걸립니다)"

PROGRESS_FILE="$TEMP_DIR/ffmpeg_progress.log"

ffmpeg -y \
    -i "$MERGED_VIDEO" \
    -i "$MERGED_AUDIO" \
    -vf "${DRAWTEXT_FILTERS}" \
    -c:v libx264 -preset medium -crf 18 \
    -c:a aac -b:a 192k \
    -shortest \
    -pix_fmt yuv420p \
    -progress "$PROGRESS_FILE" \
    "$OUTPUT_FILE" 2>"$TEMP_DIR/ffmpeg_final.log" &

FFMPEG_PID=$!

# 진행도 표시
while kill -0 $FFMPEG_PID 2>/dev/null; do
    if [ -f "$PROGRESS_FILE" ]; then
        CURRENT=$(grep -o 'out_time_ms=[0-9]*' "$PROGRESS_FILE" | tail -1 | cut -d= -f2)
        if [ -n "$CURRENT" ] && [ "$CURRENT" -gt 0 ] 2>/dev/null; then
            CURRENT_SEC=$(echo "$CURRENT / 1000000" | bc)
            TOTAL_SEC=$(echo "$TOTAL_DURATION" | bc | cut -d. -f1)
            if [ "$TOTAL_SEC" -gt 0 ] 2>/dev/null; then
                PCT=$(echo "$CURRENT_SEC * 100 / $TOTAL_SEC" | bc)
                [ "$PCT" -gt 100 ] && PCT=100
                printf "\r  📊 진행도: %d%% (%d/%d초)" "$PCT" "$CURRENT_SEC" "$TOTAL_SEC"
            fi
        fi
    fi
    sleep 2
done

wait $FFMPEG_PID
echo ""

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

OUTPUT_SIZE=$(ls -lh "$OUTPUT_FILE" | awk '{print $5}')
echo "📦 파일 크기: $OUTPUT_SIZE"
echo "⏱  총 재생시간: ${TOTAL_MINS}:${TOTAL_SECS}"
echo "========================================"
