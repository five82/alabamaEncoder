from tqdm import tqdm

from alabamaEncode.ffmpeg import Ffmpeg
from alabamaEncode.path import PathAlabama


class WrongFrameCountError(Exception):
    def __init__(self, expected_frame_count: int = -1, actual_frame_count: int = -1):
        # super().__init__("The frame count of the chunk is not equal to the expected")
        super().__init__(
            f"Expected {expected_frame_count} frames, got {actual_frame_count}"
        )


class FfmpegDecodeFailException(Exception):
    def __init__(self):
        super().__init__("Ffmpeg failed to decode the video")


class ChunkObject:
    """
    Ffmpeg based video chunk object
    example:
    If we want to extract a chunk that starts at frame 100 and ends at frame 200:
    obj = ChunkObject(100,200,"infile.mkv")
    f'ffmpeg {obj.get_ss_ffmpeg_command_pair()} outfile.mkv'
    """

    def __init__(
        self,
        first_frame_index=-1,
        last_frame_index=-1,
        path="",
        framerate=-1,
        chunk_index=-1,
        height=-1,
        width=-1,
        complexity=-1.0,
    ):
        self.path = path  # path to the source video file
        self.last_frame_index = last_frame_index
        self.first_frame_index = first_frame_index
        self.framerate = framerate
        self.width = width
        self.height = height
        self.complexity = complexity
        self.length = self.last_frame_index - self.first_frame_index
        self.end_override = -1  # ends the chunk after `end_override` frames if set
        self.chunk_index = chunk_index
        self.chunk_path = ""  # path to the encoded (or not yet) chunk file
        self.chunk_done = False
        self.ideal_bitrate = -1

    def __str__(self):
        return f"ChunkObject({self.first_frame_index}, {self.last_frame_index}, {self.path}, {self.framerate})"

    def get_frame_count(self) -> int:
        return self.last_frame_index - self.first_frame_index

    def get_lenght(self) -> float:
        # get framerate
        if self.framerate == -1:
            self.framerate = Ffmpeg.get_video_frame_rate(PathAlabama(self.path))

        local_overriden_end = self.last_frame_index
        if self.end_override != -1 and self.length > self.end_override:
            local_overriden_end = self.first_frame_index + self.end_override

        end_thingy = float(local_overriden_end) / self.framerate
        start_time = float(self.first_frame_index) / self.framerate
        return end_thingy - start_time

    def get_ss_ffmpeg_command_pair(self) -> str:
        """
        :return: an '-ss 12 clip.mp4 -t 2' ffmpeg command from start frame index and end frame index
        """

        # case where we don't have a start or end frame index so include the whole video
        if self.first_frame_index == -1 or self.last_frame_index == -1:
            return f' -i "{self.path}" '

        # get framerate
        if self.framerate == -1:
            self.framerate = Ffmpeg.get_video_frame_rate(PathAlabama(self.path))

        # if we override the end, we end at "start frame # + override"
        local_overriden_end = self.last_frame_index
        if self.end_override != -1 and self.length > self.end_override:
            local_overriden_end = self.first_frame_index + self.end_override

        # get the start time and duration
        end_thingy = float(local_overriden_end) / self.framerate
        start_time = float(self.first_frame_index) / self.framerate
        duration = end_thingy - start_time

        return f' -ss {str(start_time)} -i "{self.path}" -t {str(duration)} '

    def get_width(self) -> int:
        if self.width == -1:
            self.width = Ffmpeg.get_width(PathAlabama(self.path))
        return self.width

    def get_height(self) -> int:
        if self.height == -1:
            self.height = Ffmpeg.get_height(PathAlabama(self.path))
        return self.height

    def create_chunk_ffmpeg_pipe_command(self, video_filters="", bit_depth=10) -> str:
        """
        :param video_filters: ffmpeg vf filters, e.g., scaling tonemapping
        :param bit_depth: bit depth of the output stream 8 or 10
        :return: a 'ffmpeg ... |' command string that pipes a y4m stream into stdout
        """
        end_command = f"ffmpeg -v error -nostdin {self.get_ss_ffmpeg_command_pair()} -pix_fmt yuv420p10le "

        if bit_depth == 8:
            end_command = end_command.replace("10le", "")

        if not "-vf" in video_filters and not video_filters == "":
            video_filters = f"-vf {video_filters}"

        end_command += f" -an -sn -strict -1 {video_filters} -f yuv4mpegpipe - "

        return end_command

    def log_prefix(self):
        return f"[{self.chunk_index}] "

    def verify_integrity(self, length_of_sequence=-1) -> bool:
        """
        checks the integrity of a chunk
        :return: True if invalid
        """
        self.chunk_done = False

        try:
            path = PathAlabama(self.chunk_path)
            if Ffmpeg.check_for_invalid(path):
                raise FfmpegDecodeFailException()

            actual_frame_count = Ffmpeg.get_frame_count(path)
            expected_frame_count = self.last_frame_index - self.first_frame_index

            if actual_frame_count != expected_frame_count:
                if (
                    length_of_sequence != -1
                    and length_of_sequence == self.chunk_index + 1
                ):
                    print(
                        f"{self.log_prefix()}Frame count mismatch, but it's the last chunk, so it's ok"
                    )
                else:
                    raise WrongFrameCountError(
                        actual_frame_count=actual_frame_count,
                        expected_frame_count=expected_frame_count,
                    )
        except Exception as e:
            if isinstance(e, WrongFrameCountError) or isinstance(
                e, FfmpegDecodeFailException
            ):
                tqdm.write(f"{self.log_prefix()} failed the integrity because: {e} 🤕")
            return True

        self.chunk_done = True
        return False

    def is_done(self) -> bool:
        """
        checks if the chunk is done
        :return: True if done
        """
        self.verify_integrity()
        return self.chunk_done


def test_1():
    chunk = ChunkObject(
        path="video.mkv", first_frame_index=100, last_frame_index=200, framerate=24
    )
    expected_1 = " -ss 4.166666666666667 -i video.mkv -t 4.166666666666667 "
    result_1 = chunk.get_ss_ffmpeg_command_pair()
    print(result_1)
    if expected_1 == result_1:
        print("Test 1 passed")

    expected_2 = " -ss 4.166666666666667 -i video.mkv -t 4.166666666666667 "
    chunk.end_override = 150
    result_2 = chunk.get_ss_ffmpeg_command_pair()
    print(result_2)
    if result_2 == expected_2:
        print("Test 2 passed")

    expected_3 = " -ss 4.166666666666667 -i video.mkv -t 2.083333333333333 "
    chunk.end_override = 50
    result_3 = chunk.get_ss_ffmpeg_command_pair()
    print(result_3)
    if result_3 == expected_3:
        print("Test 3 passed")


def test_2():
    chunk = ChunkObject(path="video.mkv")
    expected_1 = " -i video.mkv "
    result_1 = chunk.get_ss_ffmpeg_command_pair()
    print(result_1)
    if expected_1 == result_1:
        print("Test 1 passed")
