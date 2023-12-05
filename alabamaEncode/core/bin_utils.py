"""
Provides a central place to get the path to the binaries, and checks if they exist.
Also includes checks if the binaries are what we need (e.g., ffmpeg has been compiled with certain flags)
"""

import os
from shutil import which

__all__ = ["get_binary", "register_bin"]

from typing import List

from alabamaEncode.core.cli_executor import run_cli

bins = []


def _check_bin(path) -> bool:
    if path is None:
        return False
    _which = which(path) is not None
    if _which:
        return True
    else:
        if os.path.exists(path):
            return True
        else:
            return False


class FFmpegNotCompiledWithLibrary(Exception):
    def __init__(self, lib_name):
        self.lib_name = lib_name

    def __str__(self):
        return f"ffmpeg is not compiled with {self.lib_name}"


def check_ffmpeg_libraries(lib_name: str) -> bool:
    """
    Checks if the ffmpeg libraries are compiled with the given library
    :param lib_name: name of the library
    :return: True if the library is compiled, False otherwise
    """
    return (
        run_cli(f"ffmpeg -v error -buildconf").verify().get_output().find(lib_name)
        != -1
    )


def verify_ffmpeg_library(lib_name: [str | List[str]]) -> None:
    """
    Checks if the ffmpeg libraries are compiled with the given library, and raises an exception if it is not
    :param lib_name: name of the library
    """
    if isinstance(lib_name, str):
        lib_name = [lib_name]
    for lib in lib_name:
        if not check_ffmpeg_libraries(lib):
            raise FFmpegNotCompiledWithLibrary(lib_name)


class BinaryNotFound(Exception):
    def __init__(self, name):
        self.name = name

    def __str__(self):
        return f"Binary {self.name} not found"


def register_bin(name, cli):
    bins.append((name, cli))


def get_binary(name):
    _bin = os.getenv(f"{name.upper()}_CLI_PATH", name)
    if _bin == name:
        for _name, _cli in bins:
            if _name == name:
                _bin = _cli
    if _check_bin(_bin):
        return _bin
    else:
        raise BinaryNotFound(name)
