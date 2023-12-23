import asyncio
import os
import random
import socket
import time

import psutil
import requests

from alabamaEncode.adaptive.executor import AdaptiveCommand
from alabamaEncode.conent_analysis.sequence_pipeline import run_sequence_pipeline
from alabamaEncode.core.ffmpeg import Ffmpeg
from alabamaEncode.core.final_touches import (
    print_stats,
    generate_previews,
    create_torrent_file,
)
from alabamaEncode.core.path import PathAlabama
from alabamaEncode.parallelEncoding.CeleryApp import app
from alabamaEncode.parallelEncoding.execute_commands import execute_commands
from alabamaEncode.scene.concat import VideoConcatenator
from alabamaEncode.scene.sequence import ChunkSequence
from alabamaEncode.scene.split import get_video_scene_list_skinny


class AlabamaEncodingJob:
    def __init__(self, ctx):
        self.ctx = ctx
        self.current_step_callback = None
        self.proc_done_callback = None
        self.finished_callback = None
        self.proc_done = 0
        self.current_step_name = "idle"

    def is_done(self):
        return self.proc_done == 100

    last_update = None

    async def update_website(self):
        api_url = os.environ["status_update_api_url"]
        token = os.environ["status_update_api_token"]
        if api_url != "":
            if token == "":
                print("Url is set, but token is not, not updating status api")
                return

            if self.ctx.title == "":
                print("Url is set, but title is not, not updating status api")
                return

            #         curl -X POST -d '{"action":"update","data":{"img":"https://domain.com/poster.avif","status":100,
            #         "title":"Show 2024 E01S01","phase":"Done"}}'
            #         -H 'Authorization: Bearer token' 'https://domain.com/update'

            data = {
                "action": "update",
                "data": {
                    "img": self.ctx.poster_url,
                    "status": round(self.proc_done, 1),  # rounded
                    "title": self.ctx.title,
                    "phase": self.current_step_name,
                },
            }

            if self.last_update == data:
                return

            self.last_update = data

            try:
                requests.post(
                    api_url + "/statuses/update",
                    json=data,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except Exception as e:
                self.ctx.log(f"Failed to update status api: {e}")

            self.ctx.log("Updated status api")

            #  curl -X POST -d '{"action":"update","data":{"id":"kokoniara-B550MH",
            #  "status":"working on title", "utilization":95}}' -H 'Authorization: Bearer token'
            #  'http://domain.com/workers/update'

            data = {
                "action": "update",
                "data": {
                    "id": (socket.gethostname()),
                    "status": f"Working on {self.ctx.title}",
                    "utilization": int(psutil.cpu_percent()),
                },
            }

            try:
                requests.post(
                    api_url + "/workers/update",
                    json=data,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except Exception as e:
                self.ctx.log(f"Failed to worker update status api: {e}")

            self.ctx.log("Updated worker status api")

    def update_current_step_name(self, step_name):
        self.current_step_name = step_name
        if self.current_step_callback is not None:
            self.current_step_callback(step_name)

        asyncio.create_task(self.update_website())

    update_proc_throttle = 0
    update_max_freq_sec = 1600

    def update_proc_done(self, proc_done):
        self.proc_done = proc_done
        if self.proc_done_callback is not None:
            self.proc_done_callback(proc_done)

        # update max every minute the proc done

        if time.time() - self.update_proc_throttle > self.update_max_freq_sec:
            self.update_proc_throttle = time.time()
            asyncio.create_task(self.update_website())

    async def run_pipeline(self):
        if self.ctx.use_celery:
            print("Using celery")
            import socket

            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                # doesn't even have to be reachable
                s.connect(("10.255.255.255", 1))
                host_address = s.getsockname()[0]
            finally:
                s.close()
            print(f"Got lan ip: {host_address}")

            num_workers = app.control.inspect().active_queues()
            if num_workers is None:
                print("No workers detected, please start some")
                quit()
            print(f"Number of available workers: {len(num_workers)}")
        else:
            print(
                f"Using {self.ctx.prototype_encoder.get_enum()} version: {self.ctx.prototype_encoder.get_version()}"
            )

        if not os.path.exists(self.ctx.output_file):
            self.update_current_step_name("Running scene detection")
            sequence: ChunkSequence = get_video_scene_list_skinny(
                input_file=self.ctx.input_file,
                cache_file_path=self.ctx.temp_folder + "sceneCache.pt",
                max_scene_length=self.ctx.max_scene_length,
                start_offset=self.ctx.start_offset,
                end_offset=self.ctx.end_offset,
                override_bad_wrong_cache_path=self.ctx.override_scenecache_path_check,
            )
            sequence.setup_paths(
                temp_folder=self.ctx.temp_folder,
                extension=self.ctx.get_encoder().get_chunk_file_extension(),
            )

            self.update_proc_done(10)
            self.update_current_step_name("Analyzing content")
            run_sequence_pipeline(self.ctx, sequence)
            chunks_sequence = sequence

            self.update_proc_done(20)
            self.update_current_step_name("Encoding scenes")

            iter_counter = 0
            if self.ctx.dry_run:
                iter_counter = 2
            while chunks_sequence.sequence_integrity_check() is True:
                iter_counter += 1
                if iter_counter > 3:
                    print("Integrity check failed 3 times, aborting")
                    quit()

                try:
                    command_objects = []
                    ctx = self.ctx

                    for chunk in sequence.chunks:
                        if not chunk.is_done():
                            command_objects.append(AdaptiveCommand(ctx, chunk))

                    # order chunks based on order
                    if ctx.chunk_order == "random":
                        random.shuffle(command_objects)
                    elif ctx.chunk_order == "length_asc":
                        command_objects.sort(key=lambda x: x.job.chunk.length)
                    elif ctx.chunk_order == "length_desc":
                        command_objects.sort(
                            key=lambda x: x.job.chunk.length, reverse=True
                        )
                    elif ctx.chunk_order == "sequential":
                        pass
                    elif ctx.chunk_order == "sequential_reverse":
                        command_objects.reverse()
                    else:
                        raise ValueError(f"Invalid chunk order: {ctx.chunk_order}")

                    if len(command_objects) < 6:
                        ctx.prototype_encoder.threads = os.cpu_count()

                    print(
                        f"Starting encoding of {len(command_objects)} out of {len(sequence.chunks)} scenes"
                    )

                    already_done = len(sequence.chunks) - len(command_objects)

                    def update_proc_done(num_finished_scenes):
                        # map 20 to 95% as the space where the scenes are encoded
                        self.update_proc_done(
                            20
                            + (already_done + num_finished_scenes)
                            / len(sequence.chunks)
                            * 75
                        )

                    await execute_commands(
                        ctx.use_celery,
                        command_objects,
                        ctx.multiprocess_workers,
                        pin_to_cores=ctx.pin_to_cores,
                        finished_scene_callback=update_proc_done,
                    )

                except KeyboardInterrupt:
                    print("Keyboard interrupt, stopping")
                    # kill all async tasks
                    for task in asyncio.all_tasks():
                        task.cancel()
                    quit()

            self.update_proc_done(95)
            self.update_current_step_name("Concatenating scenes")

            try:
                VideoConcatenator(
                    output=self.ctx.output_file,
                    file_with_audio=self.ctx.input_file,
                    audio_param_override=self.ctx.audio_params,
                    start_offset=self.ctx.start_offset,
                    end_offset=self.ctx.end_offset,
                    title=self.ctx.title,
                    encoder_name=self.ctx.encoder_name,
                    mux_audio=self.ctx.encode_audio,
                    subs_file=[self.ctx.sub_file],
                ).find_files_in_dir(
                    folder_path=self.ctx.temp_folder,
                    extension=self.ctx.get_encoder().get_chunk_file_extension(),
                ).concat_videos()
            except Exception as e:
                print("Concat failed 😷")
                raise e
        else:
            print("Output file exists 🤑, printing stats")

        self.update_proc_done(99)
        self.update_current_step_name("Final touches")

        print_stats(
            output_folder=self.ctx.output_folder,
            output=self.ctx.output_file,
            input_file=self.ctx.raw_input_file,
            grain_synth=-1,
            title=self.ctx.title,
            cut_intro=(True if self.ctx.start_offset > 0 else False),
            cut_credits=(True if self.ctx.end_offset > 0 else False),
            croped=(True if self.ctx.crop_string != "" else False),
            scaled=(True if self.ctx.scale_string != "" else False),
            tonemaped=(
                True
                if not self.ctx.prototype_encoder.hdr
                and Ffmpeg.is_hdr(PathAlabama(self.ctx.input_file))
                else False
            ),
        )
        if self.ctx.generate_previews:
            generate_previews(
                input_file=self.ctx.output_file, output_folder=self.ctx.output_folder
            )
            create_torrent_file(
                video=self.ctx.output_file,
                encoder_name=self.ctx.encoder_name,
                output_folder=self.ctx.output_folder,
            )

        print("Cleaning up temp folder 🥺")
        for root, dirs, files in os.walk(self.ctx.temp_folder):
            # remove all folders that contain 'rate_probes'
            for name in dirs:
                if "rate_probes" in name:
                    # remove {rate probe folder}/*.ivf
                    for root2, dirs2, files2 in os.walk(self.ctx.temp_folder + name):
                        for name2 in files2:
                            if name2.endswith(".ivf"):
                                os.remove(self.ctx.temp_folder + name + "/" + name2)
            # remove all *.stat files in tempfolder
            for name in files:
                if name.endswith(".stat"):
                    # try to remove
                    os.remove(self.ctx.temp_folder + name)
        # clean empty folders in the temp folder
        for root, dirs, files in os.walk(self.ctx.temp_folder):
            for name in dirs:
                if len(os.listdir(os.path.join(root, name))) == 0:
                    os.rmdir(os.path.join(root, name))

        self.update_proc_done(100)
        self.update_current_step_name("Done")
        if self.finished_callback is not None:
            self.finished_callback()
