from alabamaEncode.core.ffmpeg import Ffmpeg
from alabamaEncode.core.path import PathAlabama


def validate_input(ctx):
    try:
        tracks = Ffmpeg.get_tracks(PathAlabama(ctx.raw_input_file))
        video_track = None
        for track in tracks:
            if track["codec_type"] == "video":
                video_track = track
                break

        if video_track is None:
            print("Cant find video track in input file")
            quit()

        hdr = True
        if (
            "bt709" in video_track["color_transfer"]
            or "unknown" in video_track["color_transfer"]
        ):
            hdr = False

        dem, num = video_track["avg_frame_rate"].split("/")
        fps_rounded = "{:.2f}".format(float(dem) / float(num))
        print(
            f"Input Video: {video_track['width']}x{video_track['height']} @ {fps_rounded} fps, {video_track['pix_fmt'].upper()}, {'HDR' if hdr else 'SDR'}"
        )

    except Exception as e:
        print("Failed parsing input file, is it a valid video file?")
        quit()
    return ctx
