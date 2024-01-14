"""
Testing the ssim dB targeting technique
"""
import copy
import os
from typing import List

from alabamaEncode.adaptive.helpers.bitrateLadder import AutoBitrateLadder
from alabamaEncode.conent_analysis.chunk.optimised_vbr import get_ideal_bitrate
from alabamaEncode.core.alabama import AlabamaContext
from alabamaEncode.experiments.util.ExperimentUtil import get_test_files
from alabamaEncode.scene.chunk import ChunkObject
from alabamaEncode.scene.sequence import ChunkSequence
from alabamaEncode.scene.split import get_video_scene_list_skinny


def test():
    source_path = get_test_files()[0]

    testing_env = "./tstSsimDb/"
    if not os.path.exists(testing_env):
        os.mkdir(testing_env)

    chunk_sequence: ChunkSequence = get_video_scene_list_skinny(
        input_file=source_path,
        cache_file_path=testing_env + "sceneCache.pt",
        max_scene_length=10,
    )

    bitrate_being_evaluated = 2000

    context = AlabamaContext()
    context.temp_folder = testing_env
    ab = AutoBitrateLadder(chunk_sequence, context)
    ab.remove_ssim_translate_cache()
    ssim_db_target = ab.get_target_ssimdb(bitrate_being_evaluated)

    config = AlabamaContext()

    config.temp_folder = testing_env
    config.ssim_db_target = ssim_db_target
    config.prototype_encoder.bitrate = bitrate_being_evaluated

    chunks = chunk_sequence.get_test_chunks_out_of_a_sequence(10)
    test_chunks: List[ChunkObject] = copy.deepcopy(chunks)
    for i, chunk in enumerate(test_chunks):
        chunk.chunk_index = i
        chunk.chunk_path = f"{testing_env}chunk{i}.ivf"

    for i in range(5):
        get_ideal_bitrate(test_chunks[i], config, show_rate_calc_log=True)


if __name__ == "__main__":
    test()
