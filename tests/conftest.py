import gc

try:
    import tracemalloc
except ImportError:  # PyPy
    pass
import functools
import importlib
import os
import subprocess
import sys
import sysconfig

import pytest


# #712: the OOM test needs the oomshim helper built next to the tests.
# It's test-only (never shipped in the wheel), so build it here when it's
# missing instead of needing a compiler line by hand. Building through
# setuptools picks the right compiler and flags per platform (MSVC on
# Windows, -bundle on macOS), same toolchain that builds ujson itself.
# Minimal images without setuptools at test time (alpine) fall back to a
# plain cc line. PyPy and GraalPy have no usable hook API (missing
# declarations / fatal abort), so don't waste time building there.
# If nothing works the test skips itself (oomshim None).
def _build_oomshim(here):
    try:
        subprocess.run(
            [sys.executable, os.path.join(here, "setup_oomshim.py")],
            cwd=here,
            check=True,
            capture_output=True,
        )
        return True
    except Exception:
        pass
    try:
        subprocess.run(
            [
                os.environ.get("CC", "cc"),
                "-shared",
                "-fPIC",
                "-I" + sysconfig.get_paths()["include"],
                os.path.join(here, "oomshim.c"),
                "-o",
                os.path.join(here, "oomshim" + sysconfig.get_config_var("EXT_SUFFIX")),
            ],
            check=True,
            capture_output=True,
        )
        return True
    except Exception:
        return False


try:
    if sys.implementation.name in ("graalpy", "pypy"):
        oomshim = None
    else:
        oomshim = importlib.import_module("oomshim")
except ImportError:
    oomshim = None
    if _build_oomshim(os.path.dirname(os.path.abspath(__file__))):
        try:
            oomshim = importlib.import_module("oomshim")
        except ImportError:
            pass


def pytest_addoption(parser, pluginmanager):
    parser.addoption(
        "--leak-max-loops",
        type=int,
        help="Run each test repeatedly until its memory consumption plateaus",
    )


def add_leak_detection(function, max_loops):

    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        try:
            # A test is considered not leaky if its high tide memory usage has not
            # increased in the last 50% of or 10 iterations (whichever is larger).
            tracemalloc.start()
            function(*args, **kwargs)
            gc.collect()
            baseline = tracemalloc.get_traced_memory()[0]
            before = baseline
            last_increase = 0
            for i in range(max_loops):
                function(*args, **kwargs)
                gc.collect()
                current, _ = tracemalloc.get_traced_memory()
                print(i, last_increase, current - baseline, current - before)
                if current <= before:
                    if i >= max(10, last_increase + i // 2):
                        break
                else:
                    last_increase = i
                    before = current
                if last_increase > max_loops // 2:
                    leaked = current - baseline
                    pytest.fail(f"{leaked}B leaked ({leaked / (i + 1)} per iteration)")
        finally:
            tracemalloc.stop()

    return wrapped


def pytest_collection_modifyitems(session, config, items):
    if config.option.leak_max_loops:
        for test in items:
            if test.get_closest_marker("skip_leak_test") is None:
                test.obj = add_leak_detection(test.obj, config.option.leak_max_loops)
