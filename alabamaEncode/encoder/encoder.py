import copy
import os
import time
from abc import abstractmethod, ABC
from typing import List

from alabamaEncode.core.cli_executor import run_cli
from alabamaEncode.core.ffmpeg import Ffmpeg
from alabamaEncode.core.path import PathAlabama
from alabamaEncode.encoder.rate_dist import EncoderRateDistribution
from alabamaEncode.encoder.stats import EncodeStats
from alabamaEncode.metrics.calc import calculate_metric
from alabamaEncode.metrics.ssim.calc import get_video_ssim
from alabamaEncode.metrics.vmaf.options import VmafOptions
from alabamaEncode.metrics.vmaf.result import VmafResult


class Encoder(ABC):
    chunk = None
    bitrate: int = None
    crf: int = None
    passes: int = 2
    video_filters: str = ""
    output_path: str = None
    speed = 4
    threads = 1
    rate_distribution = EncoderRateDistribution.CQ
    qm_enabled = False
    grain_synth = 10
    qm_min = 8
    qm_max = 15
    tile_cols = -1
    tile_rows = -1
    override_flags: str = ""

    bit_override = 10

    svt_bias_pct = 50  # 100 vbr like, 0 cbr like
    svt_open_gop = True
    keyint: int = 999999
    svt_sdc: int = 0
    svt_chroma_thing = -2
    svt_supperres_mode = 0
    svt_superres_denom = 8
    svt_superres_kf_denom = 8
    svt_superres_qthresh = 43
    svt_superres_kf_qthresh = 43
    svt_sframe_interval = 0
    svt_sframe_mode = 2
    svt_tune = 0  # tune for PsychoVisual Optimization by default
    svt_tf = 1  # temporally filtered ALT-REF frames
    svt_overlay = 0  # enable overlays
    svt_aq_mode = 2  # 0: off, 1: flat, 2: adaptive
    film_grain_denoise: (0 | 1) = 1

    color_primaries = 1
    transfer_characteristics = 1
    matrix_coefficients = 1
    maximum_content_light_level = ""
    maximum_frame_average_light_level = ""
    chroma_sample_position = 0
    svt_master_display = ""
    hdr = False

    running_on_celery = False

    def supports_float_crfs(self) -> bool:
        return False

    def setup(self, chunk, config):
        self.chunk = chunk
        self.output_path = chunk.chunk_path

        if config is not None:
            self.video_filters = config.video_filters

            self.override_flags = config.override_flags

            # encoder options
            self.crf = config.crf
            self.bitrate = config.bitrate
            self.passes = config.passes
            self.speed = config.speed
            self.grain_synth = config.grain_synth
            self.rate_distribution = config.rate_distribution
            self.threads = config.threads
            self.qm_enabled = config.qm_enabled
            self.qm_min = config.qm_min
            self.qm_max = config.qm_max
            self.tile_cols = config.tile_cols  # in log2 form
            self.tile_rows = config.tile_rows  # in log2 form

            # hdr
            self.color_primaries = config.color_primaries
            self.transfer_characteristics = config.transfer_characteristics
            self.matrix_coefficients = config.matrix_coefficients
            self.maximum_content_light_level = config.maximum_content_light_level
            self.maximum_frame_average_light_level = (
                config.maximum_frame_average_light_level
            )
            self.chroma_sample_position = config.chroma_sample_position
            self.svt_master_display = config.svt_master_display
            self.hdr = config.hdr

    def update(self, **kwargs):
        """
        Update the encoder with new values, with type checking
        """
        from alabamaEncode.scene.chunk import ChunkObject

        # Define a dictionary mapping attribute names to their types
        valid_attr_types = {
            "chunk": ChunkObject,
            "bitrate": int,
            "crf": int,
            "passes": int,
            "video_filters": str,
            "output_path": str,
            "speed": int,
            "first_pass_speed": int,
            "grain_synth": int,
            "threads": int,
            "tune": int,
            "rate_distribution": EncoderRateDistribution,
            "qm_enabled": bool,
            "qm_min": int,
            "qm_max": int,
            "override_flags": str,
        }

        # Loop over the dictionary
        for attr, attr_type in valid_attr_types.items():
            # If the attribute is present in kwargs
            if attr in kwargs:
                # Get the value of the attribute
                value = kwargs.get(attr)
                # If the value is not an instance of the correct type, raise an Exception
                if not isinstance(value, attr_type):
                    raise Exception(f"FATAL: {attr} must be a {attr_type.__name__}")

        # After all checks, update the attributes
        for attr, value in kwargs.items():
            setattr(self, attr, value)

    def run(
        self,
        override_if_exists=True,
        timeout_value=-1,
        calculate_vmaf=False,
        calcualte_ssim=False,
        vmaf_params: VmafOptions = None,
        on_frame_encoded: callable = None,
    ) -> EncodeStats:
        """
        :param calcualte_ssim: self-explanatory
        :param calculate_vmaf: self-explanatory
        :param vmaf_params: dict of vmaf params
        :param override_if_exists: if false and file already exist don't do anything
        :param timeout_value: how much (in seconds) before giving up
        :param on_frame_encoded: callback function that gets called when a frame is encoded,
        with the following parameters: frame: the frame number bitrate: bitrate so far fps: encoding fps
        :return: EncodeStats object with scores bitrate & stuff
        """
        stats = EncodeStats()

        should_encode = False

        if not os.path.exists(self.output_path):
            should_encode = True
        elif override_if_exists:
            should_encode = True
        elif not self.chunk.is_done(quiet=True):
            should_encode = True

        if not should_encode:
            print("Skipping encode, file already exists")

        if should_encode:
            if self.chunk.path is None or self.chunk.path == "":
                raise Exception("FATAL: output_path is None or empty")

            if not os.path.exists(self.chunk.path):
                raise Exception("FATAL: input file does not exist")
            if self.chunk is None:
                raise Exception("FATAL: chunk is None")
            if self.chunk.chunk_index is None:
                raise Exception("FATAL: current_scene_index is None")

            original_path = copy.deepcopy(self.output_path)

            if self.running_on_celery:
                temp_celery_path = "/tmp/celery/"
                os.makedirs(temp_celery_path, exist_ok=True)
                self.output_path = f"{temp_celery_path}{self.chunk.chunk_index}{self.get_chunk_file_extension()}"

            cli_output = []
            start = time.time()
            commands = self.get_encode_commands()

            if self.running_on_celery:
                commands.append(f'cp {self.output_path} "{original_path}"')
                commands.append(f"rm {self.output_path} {self.output_path}.stat")

            self.output_path = original_path

            for command in commands:
                parse_func = None

                times_called = 0

                has_frame_callback = (
                    self.parse_output_for_output(None) is not None
                    and self.passes == 1
                    and on_frame_encoded is not None
                )

                if has_frame_callback:
                    # We can report progress to a callback
                    output_buffer = ""

                    def parse(string):
                        nonlocal output_buffer
                        nonlocal times_called
                        output_buffer += string
                        prog = self.parse_output_for_output(output_buffer)

                        if len(prog) > 0:
                            times_called += 1
                            on_frame_encoded(prog[0], prog[1], prog[2])

                            output_buffer = ""

                    parse_func = parse

                cli_output.append(
                    run_cli(
                        command, timeout_value=timeout_value, on_output=parse_func
                    ).get_output()
                )

                num_frames = self.chunk.get_frame_count()
                if has_frame_callback and times_called < num_frames:
                    for i in range(num_frames - times_called):
                        on_frame_encoded(
                            0, 0, 0
                        )  # if the encode didnt report any farmes to the callback,
                        # call it manually for the rest of the frames

            stats.time_encoding = time.time() - start

            if (
                not os.path.exists(self.output_path)
                or os.path.getsize(self.output_path) < 100
            ):
                print("Encode command failed, output:")
                for o in cli_output:
                    if isinstance(o, str):
                        o = o.replace("\x08", "")
                        print(o)
                print("Commands: ")
                for c in self.get_encode_commands():
                    print(c)

                raise Exception("FATAL: ENCODE FAILED FILE NOT FOUND OR TOO SMALL")

            if stats.time_encoding < 1:
                stats.time_encoding = 1

        if calculate_vmaf:
            local_chunk = copy.deepcopy(
                self.chunk
            )  # we need seeking variables from the chunk but the path from the
            # encoder, since the encoder object might have changed the path
            local_chunk.chunk_path = self.output_path

            vmaf_result: VmafResult = calculate_metric(
                chunk=local_chunk,
                video_filters=self.video_filters,
                vmaf_options=vmaf_params if vmaf_params is not None else VmafOptions(),
                threads=self.threads,
            )

            stats.vmaf_result = vmaf_result
            stats.vmaf = vmaf_result.mean
            stats.vmaf_percentile_1 = vmaf_result.percentile_1
            stats.vmaf_percentile_5 = vmaf_result.percentile_5
            stats.vmaf_percentile_10 = vmaf_result.percentile_10
            stats.vmaf_percentile_25 = vmaf_result.percentile_25
            stats.vmaf_percentile_50 = vmaf_result.percentile_50
            stats.vmaf_avg = vmaf_result.mean

        if calcualte_ssim:
            ssim, ssim_db = get_video_ssim(
                self.output_path,
                self.chunk,
                video_filters=self.video_filters,
                get_db=True,
            )
            stats.ssim = ssim
            stats.ssim_db = ssim_db

        stats.size = os.path.getsize(self.output_path) / 1000
        stats.bitrate = int(
            Ffmpeg.get_total_bitrate(PathAlabama(self.output_path)) / 1000
        )

        return stats

    @abstractmethod
    def get_encode_commands(self) -> List[str]:
        """
        Abstract method overriden by encoders.
        :return: A list of cli commands to encode, according to class fields
        """
        pass

    def get_ffmpeg_pipe_command(self) -> str:
        """
        return cli command that pipes a y4m stream into stdout using the chunk object
        """
        return self.chunk.create_chunk_ffmpeg_pipe_command(
            video_filters=self.video_filters,
            bit_depth=self.bit_override,
        )

    @abstractmethod
    def get_chunk_file_extension(self) -> str:
        return ".mkv"

    @abstractmethod
    def get_version(self) -> str:
        """
        return the version of the encoder
        """
        pass

    def parse_output_for_output(self, buffer) -> [List[str] | None]:
        """
        Parse the output of the encoder and return the frame number, bitrate, and fps.
        :param buffer: The output of the encoder so far
        :return: a list of [frame, bitrate, fps], [] if no output is found, None if not implemented
        """
        return None
