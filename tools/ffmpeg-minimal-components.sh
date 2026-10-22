# Features used by FIFA Street's raw MPEG-2 movie decoder.
FFMPEG_COMPONENT_FLAGS=(
  --disable-everything
  --enable-decoder=mpeg2video
  --enable-parser=mpegvideo
  --enable-swscale
)
