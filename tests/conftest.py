import gc

try:
    import tracemalloc
except ImportError:  # PyPy
    pass
import functools
import os
import subprocess
import sysconfig

import pytest


# #712: the OOM test needs the oomshim helper built next to the tests.
# It's test-only (never shipped in the wheel), so build it here when it's
# missing instead of needing a gcc line by hand. If the build fails the
# test skips itself (oomshim stays None in test_ujson).
try:
    import oomshim
except ImportError:
    here = os.path.dirname(os.path.abspath(__file__))
    target = os.path.join(here, "oomshim" + sysconfig.get_config_var("EXT_SUFFIX"))
    try:
        subprocess.run(
            [
                os.environ.get("CC", "cc"),
                "-shared",
                "-fPIC",
                "-I" + sysconfig.get_paths()["include"],
                os.path.join(here, "oomshim.c"),
                "-o",
                target,
            ],
            check=True,
            capture_output=True,
        )
        import oomshim
    except Exception:
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
