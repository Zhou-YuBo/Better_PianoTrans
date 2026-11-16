#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SHORT_ROOT_WIN="$(cygpath -m -s "$PROJECT_ROOT")"
if [[ "$SHORT_ROOT_WIN" == *" "* ]]; then
  echo "The project path needs a Windows 8.3 alias without spaces." >&2
  exit 1
fi
ROOT="$(cygpath -u "$SHORT_ROOT_WIN")"
CACHE="$ROOT/.codex-cache/ffmpeg"
ARCHIVE="$CACHE/source/ffmpeg-8.1.2.tar.xz"
BUILD_ROOT="$ROOT/.codex-tmp/ffmpeg-build"
SOURCE_DIR="$BUILD_ROOT/ffmpeg-8.1.2"
PREFIX="$CACHE/artifact/ffmpeg-8.1.2-lgpl-win-x64"
TEMP_DIR="$ROOT/.codex-tmp/ffmpeg-tmp"

if [[ ! -f "$ARCHIVE" ]]; then
  echo "Missing verified FFmpeg source archive: $ARCHIVE" >&2
  exit 1
fi

mkdir -p "$BUILD_ROOT" "$PREFIX" "$TEMP_DIR"
if [[ ! -d "$SOURCE_DIR" ]]; then
  tar -xf "$ARCHIVE" -C "$BUILD_ROOT"
fi

cd "$SOURCE_DIR"
export TMP="$TEMP_DIR"
export TEMP="$TEMP_DIR"
export TMPDIR="$TEMP_DIR"
export CC=/ucrt64/bin/gcc
export CXX=/ucrt64/bin/g++
export AR=/ucrt64/bin/ar
export NM=/ucrt64/bin/nm
export RANLIB=/ucrt64/bin/ranlib
export STRIP=/ucrt64/bin/strip
export MAKE=/ucrt64/bin/mingw32-make
JOBS="${FFMPEG_JOBS:-4}"

if [[ -f config.mak ]]; then
  "$MAKE" distclean
fi

./configure \
  --arch=x86_64 \
  --target-os=mingw32 \
  --prefix=../../../.codex-cache/ffmpeg/artifact/ffmpeg-8.1.2-lgpl-win-x64 \
  --disable-gpl \
  --disable-version3 \
  --disable-nonfree \
  --disable-autodetect \
  --disable-debug \
  --disable-doc \
  --disable-ffplay \
  --disable-ffprobe \
  --disable-network \
  --disable-indevs \
  --disable-outdevs \
  --disable-avdevice \
  --disable-swscale \
  --disable-everything \
  --enable-avfilter \
  --enable-ffmpeg \
  --enable-protocol=file,pipe \
  --enable-demuxer=aac,flac,matroska,mov,mp3,ogg,wav \
  --enable-muxer=pcm_s16le \
  --enable-parser=aac,flac,mpegaudio,opus,vorbis \
  --enable-decoder=aac,alac,flac,mp3,mp3float,opus,vorbis \
  --enable-decoder=pcm_f32be,pcm_f32le,pcm_f64be,pcm_f64le \
  --enable-decoder=pcm_s8,pcm_s16be,pcm_s16le,pcm_s24be,pcm_s24le,pcm_s32be,pcm_s32le \
  --enable-decoder=pcm_u8,pcm_u16be,pcm_u16le,pcm_u24be,pcm_u24le,pcm_u32be,pcm_u32le \
  --enable-encoder=pcm_s16le \
  --enable-filter=aformat,anull,aresample \
  --enable-swresample \
  --extra-ldflags="-static -static-libgcc" \
  --enable-small

"$MAKE" -j"$JOBS"
"$MAKE" install
